from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from trademare.core.models import Market
from trademare.data.cache import FinancialsCache, PriceCache
from trademare.data.fields import normalize_financials
from trademare.data.quality import clean_bars
from trademare.data.store import ParquetStore

THROUGH = date(2025, 6, 30)


def series(start: str, end: str, scale: float = 1.0) -> pd.DataFrame:
    idx = pd.bdate_range(start, end)
    close = pd.Series(range(len(idx)), index=idx, dtype=float) + 100
    close *= scale
    return clean_bars(
        pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1e5})
    )


class FakeSource:
    """Serves slices of a fixed 'true' history per symbol and records calls."""

    def __init__(self, history: dict[str, pd.DataFrame]) -> None:
        self.history = history
        self.calls: list[tuple[tuple[str, ...], date, date]] = []

    def get_daily_bars_many(self, market, symbols, start, end):
        self.calls.append((tuple(symbols), start, end))
        return {
            s: self.history[s].loc[pd.Timestamp(start) : pd.Timestamp(end)]
            for s in symbols
            if s in self.history
        }


@pytest.fixture
def store(tmp_path):
    return ParquetStore(tmp_path)


def test_first_update_downloads_full_history(store):
    src = FakeSource({"AAA": series("2015-01-01", "2025-06-30")})
    report = PriceCache(src, store).update(Market.US, ["AAA"], THROUGH, history_years=2)
    assert report.updated == ["AAA"]
    bars = store.read_bars(Market.US, "AAA")
    assert bars.index[0] >= pd.Timestamp("2023-06-29") and bars.index[-1] == pd.Timestamp(THROUGH)


def test_second_update_is_noop_and_incremental_appends(store):
    history = series("2015-01-01", "2025-07-15")
    src = FakeSource({"AAA": history})
    cache = PriceCache(src, store)
    cache.update(Market.US, ["AAA"], THROUGH, history_years=2)
    assert cache.update(Market.US, ["AAA"], THROUGH, history_years=2).up_to_date == ["AAA"]

    report = cache.update(Market.US, ["AAA"], date(2025, 7, 15), history_years=2)
    assert report.updated == ["AAA"] and not report.readjusted
    _, start, _ = src.calls[-1]
    assert start >= date(2025, 6, 15)  # incremental, not full
    assert store.read_bars(Market.US, "AAA").index[-1] == pd.Timestamp("2025-07-15")


def test_recently_listed_symbol_not_redownloaded(store):
    src = FakeSource({"NEW": series("2024-11-01", "2025-06-30")})
    cache = PriceCache(src, store)
    cache.update(Market.US, ["NEW"], THROUGH, history_years=5)
    assert cache.update(Market.US, ["NEW"], THROUGH, history_years=5).up_to_date == ["NEW"]


def test_raising_history_years_triggers_full_download(store):
    src = FakeSource({"AAA": series("2015-01-01", "2025-06-30")})
    cache = PriceCache(src, store)
    cache.update(Market.US, ["AAA"], THROUGH, history_years=1)
    cache.update(Market.US, ["AAA"], THROUGH, history_years=3)
    assert store.read_bars(Market.US, "AAA").index[0] < pd.Timestamp("2023-01-01")


def test_dividend_readjustment_triggers_full_refetch(store):
    src = FakeSource({"AAA": series("2015-01-01", "2025-06-30")})
    cache = PriceCache(src, store)
    cache.update(Market.US, ["AAA"], THROUGH, history_years=2)
    src.history["AAA"] = series("2015-01-01", "2025-07-15", scale=0.97)  # history re-adjusted by 3%
    report = cache.update(Market.US, ["AAA"], date(2025, 7, 15), history_years=2)
    assert report.readjusted == ["AAA"]
    stored, truth = store.read_bars(Market.US, "AAA"), src.history["AAA"]
    assert stored["close"].iloc[0] == pytest.approx(truth.loc[stored.index[0], "close"])


def test_missing_symbol_reported_failed(store):
    report = PriceCache(FakeSource({}), store).update(Market.US, ["GONE"], THROUGH, history_years=1)
    assert report.failed == ["GONE"]


def test_get_daily_bars_reads_cache_only(store):
    cache = PriceCache(FakeSource({"AAA": series("2024-01-01", "2025-06-30")}), store)
    assert cache.get_daily_bars(Market.US, "AAA", date(2025, 1, 1), THROUGH).empty
    cache.update(Market.US, ["AAA"], THROUGH, history_years=1)
    sliced = cache.get_daily_bars(Market.US, "AAA", date(2025, 6, 2), date(2025, 6, 6))
    assert len(sliced) == 5


class FakeFinancials:
    source = "fake"

    def __init__(self):
        self.calls = []

    def get_financials(self, market, symbol):
        self.calls.append(symbol)
        if symbol == "BAD":
            raise RuntimeError("boom")
        return normalize_financials(
            pd.DataFrame(
                [dict(period_type="A", period_end="2024-12-31", available_from="2025-03-01", revenue=1.0)]
            )
        )


def test_financials_cache_refreshes_only_stale(store):
    src = FakeFinancials()
    cache = FinancialsCache(src, store, workers=2)
    report = cache.update(Market.US, ["AAA", "BAD"], max_age_days=7)
    assert report.updated == ["AAA"] and report.failed == ["BAD"]
    assert cache.update(Market.US, ["AAA"], max_age_days=7).up_to_date == ["AAA"]
    later = lambda: datetime.now(UTC) + timedelta(days=8)  # noqa: E731
    assert cache.update(Market.US, ["AAA"], max_age_days=7, now=later).updated == ["AAA"]
    assert cache.get_financials(Market.US, "AAA")["revenue"].iloc[0] == 1.0
    assert cache.get_financials(Market.US, "NONE").empty


def test_force_refresh_and_old_files_gain_new_columns(store):
    src = FakeFinancials()
    cache = FinancialsCache(src, store, workers=1)
    cache.update(Market.US, ["AAA"], max_age_days=7)
    assert cache.update(Market.US, ["AAA"], max_age_days=7, force=True).updated == ["AAA"]
    assert src.calls == ["AAA", "AAA"]
    # A file written before a field existed is read back with that column as NaN.
    old = cache.get_financials(Market.US, "AAA").drop(columns=["net_income_normalized"])
    store.write_financials(Market.US, "fake", "AAA", old)
    assert cache.get_financials(Market.US, "AAA")["net_income_normalized"].isna().all()
