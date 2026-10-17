import pandas as pd
import pytest

from tests.helpers import make_bars
from trademare.analysis.regime import RegimeLabel, compute_regime
from trademare.config.schema import StopLossConfig, TakeProfitConfig
from trademare.core.models import Direction, Market
from trademare.risk.levels import compute_levels


def index_row(close, sma50, sma200):
    return pd.Series({"close": close, "sma50": sma50, "sma200": sma200})


@pytest.mark.parametrize(
    ("row", "breadth", "label"),
    [
        (index_row(110, 105, 100), 0.7, RegimeLabel.RISK_ON),
        (index_row(90, 95, 100), 0.3, RegimeLabel.RISK_OFF),
        (index_row(101, 99, 100), 0.45, RegimeLabel.NEUTRAL),
        (None, None, RegimeLabel.NEUTRAL),
    ],
)
def test_regime(row, breadth, label):
    assert compute_regime(row, breadth).label is label


BARS = make_bars(100, vol=0.01, seed=5)


@pytest.mark.parametrize(
    ("cfg", "expected_distance"),
    [
        (StopLossConfig(method="percent", percent=5), 0.05),
        (StopLossConfig(method="fixed_amount", fixed_amount=2.0), None),
    ],
)
def test_long_stop_methods(cfg, expected_distance):
    lv = compute_levels(Market.US, Direction.LONG, BARS, cfg, TakeProfitConfig(method="r_multiple", value=2))
    assert lv.stop < lv.entry < lv.target
    if expected_distance:
        assert lv.stop == pytest.approx(lv.entry * (1 - expected_distance), abs=0.01)
    else:
        assert lv.entry - lv.stop == pytest.approx(2.0, abs=0.01)
    assert lv.target - lv.entry == pytest.approx(2 * lv.risk_per_share, abs=0.02)


def test_atr_and_swing_low_stops():
    tp = TakeProfitConfig(method="none")
    atr_lv = compute_levels(Market.US, Direction.LONG, BARS, StopLossConfig(method="atr", atr_multiple=2), tp)
    rising = make_bars(100, drift=0.004, seed=6)
    swing = compute_levels(
        Market.US, Direction.LONG, rising, StopLossConfig(method="swing_low", swing_lookback=10), tp
    )
    assert atr_lv.target is None
    assert swing.stop == pytest.approx(rising["low"].iloc[-10:].min(), abs=0.01)


def test_short_levels_mirror():
    lv = compute_levels(
        Market.IN,
        Direction.SHORT,
        BARS,
        StopLossConfig(method="percent", percent=4),
        TakeProfitConfig(value=1.5),
    )
    assert lv.target < lv.entry < lv.stop
    assert round(lv.stop / 0.05, 6) == round(lv.stop / 0.05)  # NSE tick size


def test_stop_too_tight_rejected():
    cfg = StopLossConfig(method="fixed_amount", fixed_amount=0.01)
    assert compute_levels(Market.US, Direction.LONG, BARS, cfg, TakeProfitConfig()) is None


def test_swing_stop_at_entry_rejected():
    # Closing at the 10-day low leaves no room for a swing-low stop.
    falling = make_bars(100, drift=-0.01, vol=0.001, seed=7)
    cfg = StopLossConfig(method="swing_low", swing_lookback=10)
    assert compute_levels(Market.US, Direction.LONG, falling, cfg, TakeProfitConfig()) is None
