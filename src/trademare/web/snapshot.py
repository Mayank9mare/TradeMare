"""Per-market table of price performance and key ratios for the dashboard.

Computed from the local cache only and memoised until the cache changes.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from trademare.analysis.ratios import RATIO_KEYS, key_ratios
from trademare.core.models import Market
from trademare.data.service import DataService
from trademare.data.universe import load_universe

# Return windows in trading sessions.
WINDOWS = {"chg_1d": 1, "chg_1m": 21, "chg_6m": 126, "chg_1y": 252}


@dataclass(frozen=True)
class SymbolRow:
    symbol: str
    name: str
    sector: str
    last_date: date | None
    close: float | None
    from_52w_high: float | None
    returns: dict[str, float | None]
    ratios: dict[str, float | None]


def price_stats(bars: pd.DataFrame) -> dict[str, float | None]:
    if bars is None or bars.empty:
        return {k: None for k in (*WINDOWS, "close", "from_52w_high")}
    close = bars["close"]
    stats: dict[str, float | None] = {"close": float(close.iloc[-1])}
    for key, n in WINDOWS.items():
        stats[key] = float(close.iloc[-1] / close.iloc[-1 - n] - 1) if len(close) > n else None
    high = bars["high"].iloc[-252:].max()
    stats["from_52w_high"] = float(close.iloc[-1] / high - 1)
    return stats


def build_snapshot(service: DataService, market: Market) -> list[SymbolRow]:
    universe = load_universe(service.config.markets[market].universe)
    financials = service.market(market).financials
    rows = []
    for item in universe.itertuples():
        bars = service.store.read_bars(market, item.symbol)
        stats = price_stats(bars)
        last = bars.index[-1].date() if bars is not None and not bars.empty else None
        fin = financials.get_financials(market, item.symbol)
        ratios = key_ratios(fin, last, stats["close"]) if last else dict.fromkeys(RATIO_KEYS)
        rows.append(
            SymbolRow(
                symbol=item.symbol,
                name=item.name,
                sector=item.sector,
                last_date=last,
                close=stats["close"],
                from_52w_high=stats["from_52w_high"],
                returns={k: stats[k] for k in WINDOWS},
                ratios=ratios,
            )
        )
    return rows


class SnapshotCache:
    """Rebuilds a market snapshot only when cached bars or financials changed."""

    def __init__(self, service: DataService) -> None:
        self.service = service
        self._lock = threading.Lock()
        self._cache: dict[Market, tuple[tuple[float, ...], list[SymbolRow]]] = {}

    def _version(self, market: Market) -> tuple[float, ...]:
        # The store writes via atomic rename, which bumps the directory mtime.
        root = Path(self.service.store.root)
        financials = root / "financials" / market.value
        sources = sorted(financials.iterdir()) if financials.exists() else []
        dirs = [root / "bars" / market.value, *sources]
        return tuple(d.stat().st_mtime if d.exists() else 0.0 for d in dirs)

    def get(self, market: Market) -> list[SymbolRow]:
        with self._lock:
            version = self._version(market)
            cached = self._cache.get(market)
            if cached is None or cached[0] != version:
                self._cache[market] = (version, build_snapshot(self.service, market))
            return self._cache[market][1]
