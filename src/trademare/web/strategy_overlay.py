"""A strategy applied to one stock's chart: its trades as markers, its stop/target and indicator lines.

Book setups run through the same ``evaluate`` and ``simulate`` as the setup study, so the chart
shows exactly the trades the study counted for this stock (except the universe's liquidity filter,
which is ignored here: the owner asked about this stock). The core strategy ranks the whole
universe, so for it the chart shows TradeMare's own signals for the stock instead.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import pandas as pd
from sqlalchemy import select

from trademare.analysis import indicators as ind
from trademare.core.models import Market
from trademare.storage.db import SignalRow
from trademare.strategy.setup_study import simulate
from trademare.strategy.setups import FAMILIES, SETUPS, SetupContext, evaluate

CORE = "core"
LINE_COLORS = ["#8b5cf6", "#0ea5e9", "#f59e0b", "#14b8a6"]
EXIT_TEXT = {
    "stop": "stop",
    "target": "target",
    "rule": "exit rule",
    "time": "time limit",
    "open": "still open",
}


def _ichimoku(b: pd.DataFrame) -> dict[str, pd.Series]:
    ich = ind.ichimoku(b)
    return {"Cloud A": ich["span_a"], "Cloud B": ich["span_b"], "Conversion": ich["conversion"]}


def _bollinger(b: pd.DataFrame) -> dict[str, pd.Series]:
    bb = ind.bollinger(b["close"])
    return {
        name: bb[col]
        for name, col in (("Upper band", "upper"), ("Middle band", "mid"), ("Lower band", "lower"))
        if col in bb
    }


# The indicator each setup is built on, drawn under its trades (the 50/200-day lines are always on).
INDICATORS: dict[str, Callable[[pd.DataFrame], dict[str, pd.Series]]] = {
    "supertrend_rsi": lambda b: {"Supertrend": ind.supertrend(b)["line"]},
    "ichimoku": _ichimoku,
    "bb_squeeze": _bollinger,
    "williams_pullback": lambda b: {"14-day average": ind.sma(b["close"], 14)},
}


def choices() -> list[tuple[str, list[tuple[str, str]]]]:
    """[(group, [(id, label)])] for the chart's strategy menu."""
    groups = [("TradeMare", [(CORE, "Core strategy (momentum + value): its signals")])]
    for family in FAMILIES:
        items = [(s.id, f"{s.name} ({s.book})") for s in SETUPS.values() if s.family == family]
        if items:
            groups.append((f"Book setups · {family}", items))
    return groups


def _line(times: list[str], series: pd.Series) -> list[dict]:
    return [
        {"time": t, "value": round(float(v), 2)} if pd.notna(v) else {"time": t}
        for t, v in zip(times, series, strict=True)
    ]


def _path(days: pd.DatetimeIndex, trades, frame: pd.DataFrame, key: str) -> pd.Series:
    """The stop (raised by any trail) or the target, drawn only while a trade is open."""
    out = pd.Series(float("nan"), index=days)
    trail = frame["trail"].to_numpy()
    target = frame["target"].to_numpy()
    for t in trades:
        i = days.get_loc(pd.Timestamp(t.signal_date))
        j, k = days.get_loc(pd.Timestamp(t.entry_date)), days.get_loc(pd.Timestamp(t.exit_date))
        level = t.stop if key == "stop" else target[i]
        if key == "target" and (level is None or math.isnan(level)):
            continue
        for d in range(j, k + 1):
            if key == "stop" and not math.isnan(trail[d]):
                level = max(level, trail[d])
            out.iloc[d] = level
    return out


def setup_overlay(
    setup_id: str,
    symbol: str,
    market: Market,
    bars: pd.DataFrame,
    window: pd.Series,
    ctx: SetupContext,
    record: dict | None = None,
) -> dict:
    setup = SETUPS[setup_id]
    frame = evaluate(setup, bars, ctx, symbol)
    eligible = pd.Series(True, index=bars.index)
    trades = simulate(setup, symbol, bars, frame, eligible, market, start=bars.index[0].date())
    b = bars[window]
    times = [d.strftime("%Y-%m-%d") for d in b.index]
    shown = set(times)
    markers = []
    for t in trades:
        if t.entry_date in shown:
            markers.append(
                {
                    "time": t.entry_date,
                    "position": "belowBar",
                    "shape": "arrowUp",
                    "buy": True,
                    "text": f"buy {t.entry:,.2f}",
                }
            )
        if t.reason != "open" and t.exit_date in shown:
            markers.append(
                {
                    "time": t.exit_date,
                    "position": "aboveBar",
                    "shape": "arrowDown",
                    "buy": False,
                    "text": f"{EXIT_TEXT.get(t.reason, t.reason)} {t.ret:+.1%}",
                }
            )
    lines = [
        {"name": name, "color": LINE_COLORS[n % len(LINE_COLORS)], "data": _line(times, series[window])}
        for n, (name, series) in enumerate((INDICATORS.get(setup_id, lambda _: {})(bars)).items())
    ]
    lines.append(
        {
            "name": "Setup stop",
            "color": "#dc2626",
            "dashed": True,
            "data": _line(times, _path(bars.index, trades, frame, "stop")[window]),
        }
    )
    if setup.target_r is not None or frame["target"].notna().any():
        lines.append(
            {
                "name": "Setup target",
                "color": "#16a34a",
                "dashed": True,
                "data": _line(times, _path(bars.index, trades, frame, "target")[window]),
            }
        )
    in_window = [t for t in trades if t.entry_date in shown]
    closed = [t for t in in_window if t.reason != "open"]
    wins = sum(1 for t in closed if t.ret > 0)
    growth = 1.0
    for t in closed:
        growth *= 1 + t.ret
    last = frame.iloc[-1]
    today = None
    if bool(last["signal"]):
        today = {
            "entry": round(float(last["entry"]), 2),
            "stop": round(float(last["stop"]), 2),
            "target": None if pd.isna(last["target"]) else round(float(last["target"]), 2),
        }
    open_trade = next((t for t in trades if t.reason == "open"), None)
    rec = (record or {}).get(setup_id) or {}
    return {
        "name": setup.name,
        "summary": setup.summary,
        "exit": setup.exit_text,
        "lines": lines,
        "markers": markers,
        "stats": {
            "trades": len(in_window),
            "closed": len(closed),
            "wins": wins,
            "avg_ret": (sum(t.ret for t in closed) / len(closed)) if closed else None,
            "compounded": growth - 1 if closed else None,
        },
        "trades": [
            {
                "entry_date": t.entry_date,
                "entry": t.entry,
                "stop": t.stop,
                "exit_date": t.exit_date,
                "exit": t.exit,
                "reason": EXIT_TEXT.get(t.reason, t.reason),
                "days": t.days,
                "ret": t.ret,
            }
            for t in reversed(in_window)
        ],
        "today": today,
        "open": None
        if open_trade is None
        else {"entry_date": open_trade.entry_date, "entry": open_trade.entry},
        "record": {"passes": rec.get("passes"), "verdict": rec.get("verdict")} if rec else None,
    }


def core_overlay(db, market: Market, symbol: str, days: set[str]) -> dict:
    """The core strategy ranks all stocks together; for one stock, show the signals it gave."""
    markers, rows = [], []
    with db.session() as s:
        q = select(SignalRow).where(SignalRow.market == market.value, SignalRow.symbol == symbol)
        for sig in s.scalars(q.where(SignalRow.strategy.is_(None)).order_by(SignalRow.scan_date)):
            day = sig.scan_date.isoformat()
            buy = sig.action == "ENTRY"
            rows.append(
                {
                    "date": day,
                    "action": "buy" if buy else "sell",
                    "price": sig.entry_price,
                    "stop": sig.stop_price,
                    "status": sig.status.lower(),
                    "why": sig.exit_reason,
                }
            )
            if day in days:
                markers.append(
                    {
                        "time": day,
                        "position": "belowBar" if buy else "aboveBar",
                        "shape": "circle",
                        "buy": buy,
                        "text": f"{'buy' if buy else 'sell'} signal",
                    }
                )
    return {
        "name": "Core strategy",
        "summary": "Ranks every stock by 12-1 month momentum and earnings yield and buys the top ones; "
        "the chart shows the signals it gave for this stock (circles).",
        "exit": "Sells when the stock drops out of the top ranks, the trailing stop is hit, or the market "
        "turns weak.",
        "lines": [],
        "markers": markers,
        "signals": list(reversed(rows)),
    }


def window_mask(bars: pd.DataFrame, years: float) -> pd.Series:
    start = bars.index[-1] - pd.DateOffset(days=round(365.25 * years))
    return pd.Series(bars.index >= start, index=bars.index)
