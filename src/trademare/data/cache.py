"""Cache-backed data access.

``update_*`` methods hit the network and refresh the local store; ``get_*``
methods read only from the store, so analysis and backtests are fast,
reproducible and work offline.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

import pandas as pd

from trademare.core.models import Market
from trademare.data.fields import empty_financials
from trademare.data.quality import clean_bars, empty_bars
from trademare.data.store import ParquetStore

log = logging.getLogger(__name__)

OVERLAP_DAYS = 10  # re-download a few recent days to catch late corrections
ADJUSTMENT_TOLERANCE = 0.005  # >0.5% close mismatch => dividend/split re-adjustment


class BulkPriceSource(Protocol):
    def get_daily_bars_many(
        self, market: Market, symbols: list[str], start: date, end: date
    ) -> dict[str, pd.DataFrame]: ...


class FinancialsSource(Protocol):
    source: str

    def get_financials(self, market: Market, symbol: str) -> pd.DataFrame: ...


@dataclass
class UpdateReport:
    updated: list[str] = field(default_factory=list)
    up_to_date: list[str] = field(default_factory=list)
    readjusted: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{len(self.updated)} updated", f"{len(self.up_to_date)} up to date"]
        if self.readjusted:
            parts.append(f"{len(self.readjusted)} re-adjusted")
        if self.failed:
            parts.append(f"{len(self.failed)} failed")
        return ", ".join(parts)


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


class PriceCache:
    def __init__(self, source: BulkPriceSource, store: ParquetStore) -> None:
        self.source = source
        self.store = store

    def get_daily_bars(self, market: Market, symbol: str, start: date, end: date) -> pd.DataFrame:
        bars = self.store.read_bars(market, symbol)
        if bars is None:
            return empty_bars()
        return bars.loc[pd.Timestamp(start) : pd.Timestamp(end)]

    def update(
        self,
        market: Market,
        symbols: list[str],
        through: date,
        history_years: int,
        batch_size: int = 50,
    ) -> UpdateReport:
        """Bring cached bars for ``symbols`` up to the ``through`` session."""
        report = UpdateReport()
        full_start = through - timedelta(days=round(365.25 * history_years))
        full: list[str] = []  # no usable cache: download the whole history
        incremental: dict[str, date] = {}  # symbol -> last cached session
        for symbol in symbols:
            cached = self.store.read_bars(market, symbol)
            requested_from = self.store.bars_history_start(market, symbol)
            if requested_from is None and cached is not None and not cached.empty:
                requested_from = cached.index[0].date()
            # Re-download everything if history_years was raised since the last full fetch.
            if cached is None or cached.empty or requested_from > full_start + timedelta(days=7):
                full.append(symbol)
            elif cached.index[-1] >= pd.Timestamp(through):
                report.up_to_date.append(symbol)
            else:
                incremental[symbol] = cached.index[-1].date()

        refetch: list[str] = []
        if incremental:
            start = min(incremental.values()) - timedelta(days=OVERLAP_DAYS)
            refetch = self._fetch(market, sorted(incremental), start, through, batch_size, report, merge=True)
        before = len(report.updated)
        self._fetch(market, full + refetch, full_start, through, batch_size, report, merge=False)
        self.store.set_bars_history_start(market, report.updated[before:], full_start)
        report.readjusted = sorted(refetch)
        return report

    def _fetch(
        self,
        market: Market,
        symbols: list[str],
        start: date,
        end: date,
        batch_size: int,
        report: UpdateReport,
        merge: bool,
    ) -> list[str]:
        """Download and store; returns symbols needing a full re-download."""
        refetch: list[str] = []
        for batch in _chunks(symbols, batch_size):
            try:
                fetched = self.source.get_daily_bars_many(market, batch, start, end)
            except Exception:
                log.exception("%s: price batch failed (%s..)", market, batch[0])
                report.failed += batch
                continue
            for symbol in batch:
                new = fetched.get(symbol)
                if new is None or new.empty:
                    report.failed.append(symbol)
                    continue
                if merge:
                    cached = self.store.read_bars(market, symbol)
                    if cached is not None and _adjustment_changed(cached, new):
                        refetch.append(symbol)
                        continue
                    new = clean_bars(pd.concat([cached, new]) if cached is not None else new, symbol)
                self.store.write_bars(market, symbol, new)
                report.updated.append(symbol)
        return refetch


def _adjustment_changed(cached: pd.DataFrame, new: pd.DataFrame) -> bool:
    overlap = cached.index.intersection(new.index)
    if overlap.empty:
        return True  # cannot verify continuity; play safe
    old_close, new_close = cached.loc[overlap, "close"], new.loc[overlap, "close"]
    return bool(((new_close - old_close).abs() / old_close > ADJUSTMENT_TOLERANCE).any())


class FinancialsCache:
    def __init__(self, source: FinancialsSource, store: ParquetStore, workers: int = 4) -> None:
        self.source = source
        self.store = store
        self.workers = workers

    def get_financials(self, market: Market, symbol: str) -> pd.DataFrame:
        df = self.store.read_financials(market, self.source.source, symbol)
        return empty_financials() if df is None else df

    def update(
        self,
        market: Market,
        symbols: list[str],
        max_age_days: int,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> UpdateReport:
        report = UpdateReport()
        cutoff = now() - timedelta(days=max_age_days)
        stale = []
        for symbol in symbols:
            updated_at = self.store.financials_updated_at(market, self.source.source, symbol)
            if updated_at is not None and updated_at >= cutoff:
                report.up_to_date.append(symbol)
            else:
                stale.append(symbol)

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(self.source.get_financials, market, s): s for s in stale}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    df = future.result()
                except Exception as e:
                    log.warning("%s %s: fundamentals failed: %s", market, symbol, e)
                    report.failed.append(symbol)
                    continue
                if df.empty:
                    report.failed.append(symbol)
                    continue
                self.store.write_financials(market, self.source.source, symbol, df)
                report.updated.append(symbol)
        return report
