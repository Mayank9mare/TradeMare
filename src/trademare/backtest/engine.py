"""Daily event-driven backtest that replays the live scanner.

Timeline for each session t (mirrors the live "scan after close, approve,
order next day" flow):

1. Exits flagged at t-1's close fill at t's open.
2. Entry ideas from t-1's close are limit orders at that close: they fill at
   the open if it is through the limit, at the limit if t's range reaches it,
   otherwise they expire unfilled.
3. Stops and targets are checked against t's range. A gap through the stop
   fills at the open; if stop and target are both touched, the stop wins
   (conservative — daily bars cannot tell which came first).
4. Positions are marked at t's close; trailing stops ratchet using t's close.
5. The scanner runs on data up to t's close, producing t+1's orders.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from trademare.analysis.fundamental import FundamentalScore
from trademare.analysis.indicators import atr
from trademare.analysis.regime import RegimeLabel
from trademare.backtest.costs import cost_model
from trademare.config.schema import AppConfig, Rebalance, StopLossConfig, StrategyMode, TrailMethod
from trademare.core.models import Direction, OrderSide
from trademare.execution.fills import Bar, limit_fill, slipped, stop_fill, target_fill
from trademare.risk.sizing import position_size
from trademare.strategy.scanner import Idea, ScanContext, is_rebalance_day

log = logging.getLogger(__name__)


@dataclass
class Position:
    symbol: str
    sector: str
    direction: Direction
    qty: int
    entry_price: float
    entry_date: date
    stop: float
    target: float | None
    initial_risk: float  # per share, at entry
    entry_score: float
    entry_cost: float
    bars_held: int = 0

    @property
    def sign(self) -> int:
        return 1 if self.direction is Direction.LONG else -1

    def market_value(self, price: float) -> float:
        """Signed: positive for longs, negative (a liability) for shorts."""
        return self.sign * self.qty * price


@dataclass
class Trade:
    symbol: str
    sector: str
    direction: str
    entry_date: date
    entry_price: float
    exit_date: date
    exit_price: float
    qty: int
    pnl: float  # after costs
    return_pct: float
    r_multiple: float
    costs: float
    bars_held: int
    exit_reason: str
    entry_score: float


@dataclass
class BacktestResult:
    market: str
    start: date
    end: date
    initial_capital: float
    equity: pd.Series  # daily, after costs
    benchmark: pd.Series | None  # buy-and-hold index scaled to initial capital
    exposure: pd.Series  # gross exposure / equity
    trades: list[Trade]
    open_positions: list[Position]
    settings: dict = field(default_factory=dict)


class Backtester:
    def __init__(self, ctx: ScanContext, config: AppConfig | None = None) -> None:
        self.ctx = ctx
        self.config = config or ctx.config
        self.cfg = self.config.markets[ctx.market]
        self.costs = cost_model(ctx.market)
        self._atr_cache: dict[str, pd.Series] = {}

    # Fills ---------------------------------------------------------------
    def _slipped(self, price: float, side: OrderSide) -> float:
        return slipped(price, side, self.cfg.slippage_bps)

    def _atr(self, symbol: str, sl: StopLossConfig) -> pd.Series:
        if symbol not in self._atr_cache:
            self._atr_cache[symbol] = atr(self.ctx.bars[symbol], sl.atr_period)
        return self._atr_cache[symbol]

    def run(
        self,
        start: date,
        end: date,
        progress: Callable[[int, int], None] | None = None,
    ) -> BacktestResult:
        ctx, cfg, strat = self.ctx, self.cfg, self.config.strategy
        sessions = ctx.sessions(start, end)
        if len(sessions) < 2:
            raise ValueError(f"not enough sessions between {start} and {end}")
        initial = cfg.budget
        cash = initial
        positions: dict[str, Position] = {}
        pending_entries: list[Idea] = []
        pending_exits: dict[str, str] = {}
        trades: list[Trade] = []
        equity_curve, exposure_curve = {}, {}
        fundamentals: dict[str, FundamentalScore] | None = None
        last_fund_refresh: pd.Timestamp | None = None
        borrow_daily = self.config.backtest.short_borrow_rate_pct / 100 / 252
        equity = initial
        factor_mode = cfg.strategy_mode is StrategyMode.FACTOR
        rebalance = strat.factor_rebalance if factor_mode else Rebalance.DAILY
        max_positions = cfg.max_open_positions

        def bar(symbol: str, ts: pd.Timestamp) -> pd.Series | None:
            bars = ctx.bars.get(symbol)
            if bars is None or ts not in bars.index:
                return None
            return bars.loc[ts]

        def close_position(pos: Position, price: float, ts: pd.Timestamp, reason: str) -> None:
            nonlocal cash
            side = OrderSide.SELL if pos.direction is Direction.LONG else OrderSide.BUY
            exit_cost = self.costs.cost(side, price, pos.qty)
            cash += pos.market_value(price) - exit_cost
            gross = pos.sign * (price - pos.entry_price) * pos.qty
            total_cost = pos.entry_cost + exit_cost
            pnl = gross - total_cost
            trades.append(
                Trade(
                    symbol=pos.symbol,
                    sector=pos.sector,
                    direction=pos.direction.value,
                    entry_date=pos.entry_date,
                    entry_price=pos.entry_price,
                    exit_date=ts.date(),
                    exit_price=price,
                    qty=pos.qty,
                    pnl=pnl,
                    return_pct=pnl / (pos.entry_price * pos.qty),
                    r_multiple=pnl / (pos.initial_risk * pos.qty) if pos.initial_risk > 0 else 0.0,
                    costs=total_cost,
                    bars_held=pos.bars_held,
                    exit_reason=reason,
                    entry_score=pos.entry_score,
                )
            )
            del positions[pos.symbol]

        for i, ts in enumerate(sessions):
            today = ts.date()

            # 1. Exits flagged at the previous close fill at today's open.
            for symbol, reason in list(pending_exits.items()):
                pos, b = positions.get(symbol), bar(symbol, ts)
                if pos is not None and b is not None:
                    side = OrderSide.SELL if pos.direction is Direction.LONG else OrderSide.BUY
                    close_position(pos, self._slipped(float(b["open"]), side), ts, reason)
                    pending_exits.pop(symbol)

            # 2. Limit entries from the previous close.
            fill_prices: dict[str, float] = {}
            for idea in pending_entries:
                if idea.symbol in positions or len(positions) >= max_positions:
                    continue
                b = bar(idea.symbol, ts)
                if b is None:
                    continue
                limit, stop = idea.levels.entry, idea.levels.stop
                long = idea.direction is Direction.LONG
                fill = limit_fill(OrderSide.BUY if long else OrderSide.SELL, limit, _bar(b))
                if fill is None:
                    continue  # limit not reached: order expires
                if (long and fill <= stop) or (not long and fill >= stop):
                    continue  # gapped through the stop before we could enter
                gross = sum(abs(p.market_value(p.entry_price)) for p in positions.values())
                sector = sum(
                    abs(p.market_value(p.entry_price)) for p in positions.values() if p.sector == idea.sector
                )
                capital = equity if self.config.backtest.compound else min(equity, initial)
                qty = position_size(fill, stop, capital, cash, cfg, sector, gross)
                if qty <= 0:
                    continue
                side = OrderSide.BUY if long else OrderSide.SELL
                entry_cost = self.costs.cost(side, fill, qty)
                cash -= (qty * fill if long else -qty * fill) + entry_cost
                positions[idea.symbol] = Position(
                    symbol=idea.symbol,
                    sector=idea.sector,
                    direction=idea.direction,
                    qty=qty,
                    entry_price=fill,
                    entry_date=today,
                    stop=stop,
                    target=idea.levels.target,
                    initial_risk=abs(fill - stop),
                    entry_score=idea.composite,
                    entry_cost=entry_cost,
                )
                fill_prices[idea.symbol] = fill
            pending_entries = []

            # 3. Stops and targets within today's range.
            for pos in list(positions.values()):
                b = bar(pos.symbol, ts)
                if b is None:
                    continue
                # A position opened today at its limit could not have seen the open.
                opened_at = fill_prices.get(pos.symbol)
                exit_side = OrderSide.SELL if pos.direction is Direction.LONG else OrderSide.BUY
                bar_today = _bar(b)
                if hit := stop_fill(exit_side, pos.stop, bar_today, opened_at):
                    price, gapped = hit
                    close_position(
                        pos, self._slipped(price, exit_side), ts, "stop (gap)" if gapped else "stop"
                    )
                elif pos.target is not None and (
                    hit := target_fill(exit_side, pos.target, bar_today, opened_at)
                ):
                    price, gapped = hit
                    close_position(pos, price, ts, "target (gap)" if gapped else "target")

            # 4. Mark to market, borrow fees.
            marked = 0.0
            gross = 0.0
            for pos in positions.values():
                b = bar(pos.symbol, ts)
                px = float(b["close"]) if b is not None else _last_close(ctx.bars[pos.symbol], ts)
                marked += pos.market_value(px)
                gross += abs(pos.market_value(px))
                if pos.direction is Direction.SHORT:
                    cash -= abs(pos.market_value(px)) * borrow_daily
            equity = cash + marked
            equity_curve[ts] = equity
            exposure_curve[ts] = gross / equity if equity > 0 else 0.0

            if i == len(sessions) - 1:
                break  # no orders after the final session

            # 5. Trailing stops and time stops.
            sl = cfg.stop_loss
            for pos in positions.values():
                b = bar(pos.symbol, ts)
                if b is None:
                    continue
                pos.bars_held += 1
                close = float(b["close"])
                if sl.trailing:
                    if sl.trail_method is TrailMethod.ATR:
                        a = self._atr(pos.symbol, sl).get(ts)
                        distance = None if a is None or pd.isna(a) else sl.trail_value * float(a)
                    else:
                        distance = close * sl.trail_value / 100
                    if distance:
                        candidate = close - pos.sign * distance
                        if (pos.direction is Direction.LONG and candidate > pos.stop) or (
                            pos.direction is Direction.SHORT and candidate < pos.stop
                        ):
                            pos.stop = candidate
                if cfg.time_stop_days and pos.bars_held >= cfg.time_stop_days:
                    progress_r = pos.sign * (close - pos.entry_price) / pos.initial_risk
                    if progress_r < 0.5:
                        pending_exits[pos.symbol] = "time stop"

            if progress:
                progress(i + 1, len(sessions))

            # 6. On rebalance days, scan at the close for tomorrow's orders.
            if not is_rebalance_day(sessions[i - 1].date() if i else None, today, rebalance):
                continue
            if factor_mode:
                fundamentals = {}
            elif (
                last_fund_refresh is None or (ts - last_fund_refresh).days >= strat.fundamentals_refresh_days
            ):
                fundamentals = ctx.fundamental_scores(today)
                last_fund_refresh = ts
            scan = ctx.evaluate(today, fundamentals=fundamentals, keep_scores=False)
            composites = {s.symbol: s.composite for s in scan.scores}
            for pos in positions.values():
                c = composites.get(pos.symbol)
                if c is None or pos.symbol in pending_exits:
                    continue
                if (pos.direction is Direction.LONG and c <= scan.exit_long_below) or (
                    pos.direction is Direction.SHORT and c >= scan.exit_short_above
                ):
                    pending_exits[pos.symbol] = "signal"
            # Regime filter: fewer slots in risk-off; trim the weakest holdings beyond them.
            max_positions = cfg.max_open_positions
            if scan.regime.label is RegimeLabel.RISK_OFF:
                max_positions = int(cfg.max_open_positions * strat.risk_off_exposure)
            staying = [p for p in positions.values() if p.symbol not in pending_exits]
            excess = len(staying) - max_positions
            if excess > 0:
                weakest = sorted(staying, key=lambda p: p.sign * (composites.get(p.symbol) or 0.0))
                for pos in weakest[:excess]:
                    pending_exits[pos.symbol] = "regime"
            pending_entries = [idea for idea in scan.ideas if idea.symbol not in positions]

        benchmark = None
        if ctx.benchmark is not None:
            bench = ctx.benchmark["close"].reindex(sessions).ffill()
            benchmark = bench / bench.iloc[0] * initial
        return BacktestResult(
            market=ctx.market.value,
            start=sessions[0].date(),
            end=sessions[-1].date(),
            initial_capital=initial,
            equity=pd.Series(equity_curve),
            benchmark=benchmark,
            exposure=pd.Series(exposure_curve),
            trades=trades,
            open_positions=list(positions.values()),
        )


def _bar(row: pd.Series) -> Bar:
    return Bar(float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]))


def _last_close(bars: pd.DataFrame, ts: pd.Timestamp) -> float:
    return float(bars["close"].loc[:ts].iloc[-1])
