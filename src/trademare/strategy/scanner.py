"""Daily scan: score every symbol in a market's universe and propose trade ideas.

Uses cached data only and nothing published after ``asof``, so the same code
drives live scans and (in phase 3) backtests.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from trademare.analysis.factors import FACTORS, combine_ranks, factor_values
from trademare.analysis.fundamental import FundamentalScore, score_universe
from trademare.analysis.ratios import finalize_ratios, statement_ratios
from trademare.analysis.regime import Regime, RegimeLabel, compute_regime
from trademare.analysis.technical import TechnicalScore, score_features, technical_features
from trademare.config.schema import AppConfig, MarketConfig, Rebalance, StrategyMode
from trademare.core.interfaces import AnalystResult
from trademare.core.models import Direction, Market
from trademare.data.service import DataService
from trademare.data.universe import load_universe
from trademare.risk.levels import Levels, compute_levels

log = logging.getLogger(__name__)


class SymbolScore(BaseModel):
    symbol: str
    name: str
    sector: str
    close: float | None
    composite: float | None
    confidence: float
    liquid: bool
    analysts: dict[str, AnalystResult] = Field(default_factory=dict)


class Idea(BaseModel):
    symbol: str
    name: str
    sector: str
    direction: Direction
    composite: float
    confidence: float
    levels: Levels
    reasons: dict[str, list[str]]

    @property
    def rank_key(self) -> float:
        return abs(self.composite) * self.confidence


class ScanResult(BaseModel):
    market: Market
    asof: date
    created_at: datetime = Field(default_factory=datetime.now)
    regime: Regime
    weights: dict[str, float]
    ideas: list[Idea]
    scores: list[SymbolScore]
    mode: str = "swing"
    # Held positions exit when their composite crosses these (long: <=, short: >=).
    exit_long_below: float = 0.0
    exit_short_above: float = 0.0
    # Volatility targeting: share of slots usable now (1.0 when off or calm).
    vol_scale: float = 1.0

    def ideas_for(self, direction: Direction) -> list[Idea]:
        return [i for i in self.ideas if i.direction is direction]


def _tech_result(t: TechnicalScore) -> AnalystResult:
    return AnalystResult(
        analyst="technical",
        score=round(t.score, 4),
        confidence=round(t.confidence, 4),
        reasons=t.reasons,
        details={k: round(v, 4) for k, v in t.components.items()},
    )


def _fund_result(f: FundamentalScore) -> AnalystResult:
    return AnalystResult(
        analyst="fundamental",
        score=round(f.score, 4),
        confidence=round(f.confidence, 4),
        reasons=f.reasons,
        details={k: (None if v is None else round(v, 4)) for k, v in f.factors.items()},
    )


def combine(results: dict[str, AnalystResult], weights: dict[str, float]) -> tuple[float | None, float]:
    """Weighted composite over the analysts present; weights are renormalised."""
    present = {k: r for k, r in results.items() if weights.get(k, 0) > 0}
    if not present:
        return None, 0.0
    total = sum(weights[k] for k in present)
    composite = sum(weights[k] * r.score for k, r in present.items()) / total
    confidence = sum(weights[k] * r.confidence for k, r in present.items()) / total
    return composite, confidence


def decide(
    score: SymbolScore,
    cfg: MarketConfig,
    config: AppConfig,
    regime: Regime,
) -> Direction | None:
    """Apply the strategy rules to one scored symbol."""
    s = config.strategy
    tech = score.analysts.get("technical")
    fund = score.analysts.get("fundamental")
    if score.composite is None or tech is None or not score.liquid:
        return None
    if s.require_fundamentals and fund is None:
        return None
    # setup < 0 means "extended / poor long entry"; > 0 means "oversold / poor short entry".
    setup = tech.details.get("setup") or 0.0
    long_entry_ok = not s.avoid_extended_entries or setup >= 0
    short_entry_ok = not s.avoid_extended_entries or setup <= 0
    long_threshold = s.min_composite_score + (s.regime_penalty if regime.label is RegimeLabel.RISK_OFF else 0)
    short_threshold = s.min_composite_score + (s.regime_penalty if regime.label is RegimeLabel.RISK_ON else 0)
    if (
        cfg.allow_long
        and long_entry_ok
        and score.composite >= long_threshold
        and tech.score > 0
        and (fund is None or fund.score >= s.min_fundamental_score)
    ):
        return Direction.LONG
    if (
        cfg.allow_short
        and short_entry_ok
        and score.composite <= -short_threshold
        and tech.score < 0
        and (fund is None or fund.score <= -s.min_fundamental_score)
    ):
        return Direction.SHORT
    return None


class ScanContext:
    """All inputs for scanning one market, loaded once.

    Indicators are computed over each symbol's full history (they are causal),
    so ``evaluate`` can be called for many dates cheaply — this is what the
    backtester does. Statement ratios are cached per set of public filings.
    """

    def __init__(self, service: DataService, market: Market) -> None:
        self.service = service
        self.config = service.config
        self.market = market
        self.cfg = self.config.markets[market]
        universe = load_universe(self.cfg.universe)
        self.symbols: list[str] = universe["symbol"].tolist()
        self.names = dict(zip(universe["symbol"], universe["name"], strict=True))
        self.sectors = dict(zip(universe["symbol"], universe["sector"], strict=True))
        store = service.store

        self.benchmark: pd.DataFrame | None = None
        self.benchmark_features: pd.DataFrame | None = None
        if self.cfg.benchmark and (b := store.read_bars(market, self.cfg.benchmark)) is not None and len(b):
            self.benchmark = b
            self.benchmark_features = technical_features(b)

        self.bars: dict[str, pd.DataFrame] = {}
        self.features: dict[str, pd.DataFrame] = {}
        # Row lookups via numpy are ~50x faster than pandas .iloc in the backtest loop.
        self._feature_arrays: dict[str, tuple[pd.DatetimeIndex, list[str], object]] = {}
        for symbol in self.symbols:
            bars = store.read_bars(market, symbol)
            if bars is None or bars.empty:
                continue
            self.bars[symbol] = bars
            f = technical_features(bars, self.benchmark)
            self.features[symbol] = f
            self._feature_arrays[symbol] = (f.index, list(f.columns), f.to_numpy())

        source = service.market(market).financials
        self.financials: dict[str, pd.DataFrame] = {}
        self._available: dict[str, pd.Series] = {}
        for symbol in self.symbols:
            fin = source.get_financials(market, symbol)
            if not fin.empty:
                self.financials[symbol] = fin
                self._available[symbol] = fin["available_from"].sort_values().reset_index(drop=True)
        self._statement_cache: dict[tuple[str, int, int], dict | None] = {}

        self._index_vol: tuple[pd.Series, pd.Series] | None = None
        self.weights = self.config.strategy.weights.model_dump()
        if not self.config.llm.enabled:
            self.weights["sentiment"] = 0.0

    def last_date(self) -> date:
        if not self.bars:
            raise ValueError(f"no cached bars for {self.market}; run: trademare data update -m {self.market}")
        return max(b.index[-1] for b in self.bars.values()).date()

    def sessions(self, start: date, end: date) -> pd.DatetimeIndex:
        """Trading days in [start, end], taken from the benchmark (or all bars)."""
        if self.benchmark is not None:
            idx = self.benchmark.index
        else:
            idx = pd.DatetimeIndex(sorted({d for b in self.bars.values() for d in b.index}))
        return idx[(idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end))]

    def feature_row(self, symbol: str, asof: pd.Timestamp) -> dict[str, float] | None:
        """Latest feature row at or before ``asof``; None if stale or not yet listed."""
        entry = self._feature_arrays.get(symbol)
        if entry is None:
            return None
        index, columns, values = entry
        i = index.searchsorted(asof, side="right") - 1
        if i < 0 or index[i] < asof - pd.Timedelta(days=7):
            return None
        return dict(zip(columns, values[i], strict=True))

    def bars_until(self, symbol: str, asof: pd.Timestamp) -> pd.DataFrame:
        return self.bars[symbol].loc[:asof]

    def ratios(self, symbol: str, asof: date, price: float) -> dict | None:
        if symbol not in self.financials:
            return None
        avail = self._available[symbol]
        # The statement ratios only change when the set of public filings changes.
        key = (
            symbol,
            int(avail.searchsorted(pd.Timestamp(asof), side="right")),
            int(avail.searchsorted(pd.Timestamp(asof) - pd.Timedelta(days=365), side="right")),
        )
        if key not in self._statement_cache:
            self._statement_cache[key] = statement_ratios(self.financials[symbol], asof)
        return finalize_ratios(self._statement_cache[key], asof, price)

    def fundamental_scores(self, asof: date) -> dict[str, FundamentalScore]:
        ts = pd.Timestamp(asof)
        ratios = {}
        for symbol in self.financials:
            row = self.feature_row(symbol, ts)
            if row is not None:
                ratios[symbol] = self.ratios(symbol, asof, float(row["close"]))
        return score_universe(ratios, self.sectors)

    def vol_scale_at(self, ts: pd.Timestamp) -> float:
        """min(1, typical / current) index volatility; 1.0 when off or without enough history.

        Current = annualised 63-session realised volatility of the benchmark; typical = its median
        over the trailing 756 sessions (at least 252). Uses data up to ``ts`` only.
        """
        if not self.config.strategy.vol_target or self.benchmark is None:
            return 1.0
        if self._index_vol is None:
            returns = self.benchmark["close"].pct_change()
            current = returns.rolling(63, min_periods=63).std() * np.sqrt(252)
            typical = current.rolling(756, min_periods=252).median()
            self._index_vol = (current, typical)
        current, typical = self._index_vol
        now, usual = current.loc[:ts], typical.loc[:ts]
        if now.empty or pd.isna(now.iloc[-1]) or usual.empty or pd.isna(usual.iloc[-1]) or now.iloc[-1] <= 0:
            return 1.0
        return float(min(1.0, usual.iloc[-1] / now.iloc[-1]))

    def regime_at(self, ts: pd.Timestamp, rows: dict[str, dict[str, float]] | None = None) -> Regime:
        """Market regime from the index trend and the share of stocks above their SMA200."""
        if rows is None:
            rows = {s: r for s in self.symbols if (r := self.feature_row(s, ts)) is not None}
        with_200 = [r for r in rows.values() if pd.notna(r["sma200"])]
        breadth = sum(r["close"] > r["sma200"] for r in with_200) / len(with_200) if with_200 else None
        index_row = None
        if self.benchmark_features is not None:
            i = self.benchmark_features.index.searchsorted(ts, side="right") - 1
            index_row = self.benchmark_features.iloc[i] if i >= 0 else None
        return compute_regime(index_row, breadth)

    def evaluate(
        self,
        asof: date,
        fundamentals: dict[str, FundamentalScore] | None = None,
        keep_scores: bool = True,
    ) -> ScanResult:
        """Score the universe and pick ideas as of ``asof`` (after that day's close)."""
        if self.cfg.strategy_mode is StrategyMode.FACTOR:
            return self._evaluate_factor(asof, keep_scores)
        return self._evaluate_swing(asof, fundamentals, keep_scores)

    def _evaluate_swing(
        self, asof: date, fundamentals: dict[str, FundamentalScore] | None, keep_scores: bool
    ) -> ScanResult:
        ts = pd.Timestamp(asof)
        fund = self.fundamental_scores(asof) if fundamentals is None else fundamentals
        rows = {s: r for s in self.symbols if (r := self.feature_row(s, ts)) is not None}
        regime = self.regime_at(ts, rows)

        scores: list[SymbolScore] = []
        ideas: list[Idea] = []
        for symbol in self.symbols:
            row = rows.get(symbol)
            results: dict[str, AnalystResult] = {}
            if row is not None and (t := score_features(row)) is not None:
                results["technical"] = _tech_result(t)
            if row is not None and symbol in fund:
                results["fundamental"] = _fund_result(fund[symbol])
            composite, confidence = combine(results, self.weights)
            liquid = (
                row is not None
                and pd.notna(row["traded_value_20"])
                and row["traded_value_20"] >= self.cfg.min_avg_traded_value
            )
            score = SymbolScore(
                symbol=symbol,
                name=self.names[symbol],
                sector=self.sectors[symbol],
                close=None if row is None else float(row["close"]),
                composite=None if composite is None else round(composite, 4),
                confidence=round(confidence, 4),
                liquid=bool(liquid),
                analysts=results,
            )
            scores.append(score)
            direction = decide(score, self.cfg, self.config, regime)
            if direction is None:
                continue
            # Reuse the precomputed 14-period ATR when the stop uses the same period.
            atr_now = row["atr"] if self.cfg.stop_loss.atr_period == 14 else None
            levels = compute_levels(
                self.market,
                direction,
                self.bars_until(symbol, ts),
                self.cfg.stop_loss,
                self.cfg.take_profit,
                atr_value=atr_now,
            )
            if levels is None:
                continue
            ideas.append(
                Idea(
                    symbol=symbol,
                    name=score.name,
                    sector=score.sector,
                    direction=direction,
                    composite=score.composite,
                    confidence=score.confidence,
                    levels=levels,
                    reasons={k: r.reasons for k, r in results.items()},
                )
            )

        limit = self.config.strategy.max_ideas
        ranked: list[Idea] = []
        for direction in Direction:
            side = sorted(
                (i for i in ideas if i.direction is direction), key=lambda i: i.rank_key, reverse=True
            )
            ranked += side[:limit]
        if not keep_scores:
            # Backtests only need composites; drop analyst detail to save memory.
            scores = [s.model_copy(update={"analysts": {}}) for s in scores if s.composite is not None]
        exit_score = self.config.strategy.exit_score
        return ScanResult(
            market=self.market,
            asof=asof,
            regime=regime,
            weights=self.weights,
            ideas=ranked,
            scores=scores,
            exit_long_below=exit_score,
            exit_short_above=-exit_score,
        )

    def _evaluate_factor(self, asof: date, keep_scores: bool) -> ScanResult:
        """Strategy v2: rank liquid symbols on the configured factors."""
        ts = pd.Timestamp(asof)
        names = self.cfg.factors
        strat = self.config.strategy
        exclude = strat.exclude_high_vol_pct
        extra = ["low_vol"] if exclude and "low_vol" not in names else []
        values = factor_values(self, asof, names + extra)
        regime = self.regime_at(ts)
        liquid = values[values["liquid"].astype(bool)] if not values.empty else values
        too_volatile: set[str] = set()
        if exclude and not liquid.empty:
            # low_vol = -volatility, so the most volatile names have the lowest percentile.
            vol_pct = liquid["low_vol"].rank(pct=True)
            too_volatile = set(vol_pct[vol_pct <= exclude].index)
        combined = combine_ranks(liquid, names) if not liquid.empty else pd.Series(dtype="float64")
        # Re-rank so composite maps exactly to a percentile: top 10% <=> composite >= 0.8.
        composite = combined.rank(pct=True) * 2 - 1
        factor_pct = {n: liquid[n].rank(pct=True) for n in names if n in liquid}
        factor_pos = {n: liquid[n].rank(ascending=False, method="min") for n in names if n in liquid}
        counts = {n: int(liquid[n].count()) for n in names if n in liquid}
        entry_cut = 1 - 2 * strat.factor_entry_pct
        exit_cut = 1 - 2 * strat.factor_exit_pct

        scores: list[SymbolScore] = []
        ideas: list[Idea] = []
        for symbol in self.symbols:
            c = composite.get(symbol)
            close = float(values.at[symbol, "close"]) if symbol in values.index else None
            analysts: dict[str, AnalystResult] = {}
            if c is not None:
                reasons, details = [], {}
                for n in names:
                    v = values.at[symbol, n]
                    if pd.isna(v):
                        continue
                    details[n] = round(float(factor_pct[n][symbol]) * 2 - 1, 4)
                    reasons.append(
                        f"{FACTORS[n].label} {FACTORS[n].display(float(v))} "
                        f"(#{int(factor_pos[n][symbol])} of {counts[n]})"
                    )
                analysts["factor"] = AnalystResult(
                    analyst="factor",
                    score=round(float(c), 4),
                    confidence=round(len(details) / len(names), 4),
                    reasons=reasons,
                    details=details,
                )
            score = SymbolScore(
                symbol=symbol,
                name=self.names[symbol],
                sector=self.sectors[symbol],
                close=close,
                composite=None if c is None else round(float(c), 4),
                confidence=analysts["factor"].confidence if analysts else 0.0,
                liquid=c is not None,
                analysts=analysts,
            )
            scores.append(score)
            if c is None:
                continue
            direction = (
                Direction.LONG
                if self.cfg.allow_long and c >= entry_cut
                else Direction.SHORT
                if self.cfg.allow_short and c <= -entry_cut
                else None
            )
            if direction is None or symbol in too_volatile:
                continue
            row = self.feature_row(symbol, ts)
            atr_now = row["atr"] if self.cfg.stop_loss.atr_period == 14 else None
            levels = compute_levels(
                self.market,
                direction,
                self.bars_until(symbol, ts),
                self.cfg.stop_loss,
                self.cfg.take_profit,
                atr_value=atr_now,
            )
            if levels is None:
                continue
            ideas.append(
                Idea(
                    symbol=symbol,
                    name=score.name,
                    sector=score.sector,
                    direction=direction,
                    composite=score.composite,
                    confidence=score.confidence,
                    levels=levels,
                    reasons={"factor": analysts["factor"].reasons},
                )
            )

        ideas.sort(key=lambda i: abs(i.composite), reverse=True)
        if not keep_scores:
            scores = [s.model_copy(update={"analysts": {}}) for s in scores if s.composite is not None]
        return ScanResult(
            market=self.market,
            asof=asof,
            regime=regime,
            weights={n: 1 / len(names) for n in names},
            ideas=ideas,
            scores=scores,
            mode="factor",
            exit_long_below=exit_cut,
            exit_short_above=-exit_cut,
            vol_scale=self.vol_scale_at(ts),
        )


def is_rebalance_day(previous: date | None, today: date, frequency: Rebalance) -> bool:
    """True on the first session of each period (``previous`` is the prior session)."""
    if previous is None or frequency is Rebalance.DAILY:
        return True
    if frequency is Rebalance.WEEKLY:
        return previous.isocalendar()[:2] != today.isocalendar()[:2]
    return (previous.year, previous.month) != (today.year, today.month)


def run_scan(service: DataService, market: Market, asof: date | None = None) -> ScanResult:
    ctx = ScanContext(service, market)
    result = ctx.evaluate(asof or ctx.last_date())
    log.info(
        "%s scan %s: %d scored, %d ideas, regime %s",
        market, result.asof, len(result.scores), len(result.ideas), result.regime.label,
    )  # fmt: skip
    return result


# Persistence: one JSON file per market per scan date.
def scan_dir(service: DataService, market: Market) -> Path:
    return Path(service.config.data.cache_dir).parent / "scans" / market.value


def save_scan(service: DataService, result: ScanResult) -> Path:
    path = scan_dir(service, result.market) / f"{result.asof.isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(result.model_dump_json())
    tmp.replace(path)
    return path


def load_latest_scan(service: DataService, market: Market) -> ScanResult | None:
    folder = scan_dir(service, market)
    files = sorted(folder.glob("*.json")) if folder.exists() else []
    if not files:
        return None
    return ScanResult.model_validate(json.loads(files[-1].read_text()))
