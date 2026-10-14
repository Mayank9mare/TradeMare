from datetime import date

import pandas as pd

from trademare.core.models import Market
from trademare.data.quality import clean_bars
from trademare.data.store import ParquetStore


def bars(rows, index):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=pd.to_datetime(index))


def test_clean_bars_drops_invalid_and_dedupes():
    df = bars(
        [
            [10, 11, 9, 10.5, 100],
            [10, 11, 9, None, 100],
            [10, 9, 11, 10, 100],
            [-1, 1, 1, 1, 1],
            [10, 12, 9, 11, 200],
        ],
        ["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07", "2025-01-02"],
    )
    out = clean_bars(df)
    assert list(out.index) == [pd.Timestamp("2025-01-02")]
    assert out.iloc[0]["close"] == 11  # last duplicate wins


def test_clean_bars_fixes_high_low_bracket():
    out = clean_bars(bars([[10, 10.2, 9.9, 10.5, 1]], ["2025-01-02"]))
    assert out.iloc[0]["high"] == 10.5 and out.iloc[0]["low"] == 9.9


def test_clean_bars_strips_timezone():
    df = bars([[1, 1, 1, 1, 1]], ["2025-01-02 00:00"])
    df.index = df.index.tz_localize("Asia/Kolkata")
    assert clean_bars(df).index.tz is None


def test_store_roundtrip_with_unsafe_symbols(tmp_path):
    store = ParquetStore(tmp_path)
    df = clean_bars(bars([[1, 2, 0.5, 1.5, 10]], ["2025-01-02"]))
    for symbol in ["M&M", "BRK.B", "BAJAJ-AUTO"]:
        store.write_bars(Market.IN, symbol, df)
        pd.testing.assert_frame_equal(store.read_bars(Market.IN, symbol), df)
    assert store.bar_symbols(Market.IN) == ["BAJAJ-AUTO", "BRK.B", "M&M"]
    assert store.read_bars(Market.US, "M&M") is None


def test_history_start_manifest(tmp_path):
    store = ParquetStore(tmp_path)
    assert store.bars_history_start(Market.US, "AAPL") is None
    store.set_bars_history_start(Market.US, ["AAPL", "MSFT"], date(2018, 1, 1))
    store.set_bars_history_start(Market.US, ["MSFT"], date(2017, 1, 1))
    assert store.bars_history_start(Market.US, "AAPL") == date(2018, 1, 1)
    assert store.bars_history_start(Market.US, "MSFT") == date(2017, 1, 1)
