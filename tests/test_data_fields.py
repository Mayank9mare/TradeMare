from datetime import date

import numpy as np
import pandas as pd
import pytest

from trademare.data.fields import COLUMNS, normalize_financials, point_in_time, ttm


def frame(rows):
    return normalize_financials(pd.DataFrame(rows))


def quarter(end, avail, revenue, net_income=1.0, equity=100.0):
    return dict(
        period_type="Q",
        period_end=end,
        available_from=avail,
        revenue=revenue,
        net_income=net_income,
        total_equity=equity,
    )


QUARTERS = [
    quarter("2025-03-31", "2025-05-10", 10),
    quarter("2025-06-30", "2025-08-10", 11),
    quarter("2025-09-30", "2025-11-10", 12),
    quarter("2025-12-31", "2026-02-10", 13, equity=120.0),
]


def test_normalize_sets_columns_and_types():
    df = frame(QUARTERS)
    assert tuple(df.columns) == COLUMNS
    assert df["revenue"].dtype == "float64"
    assert np.isnan(df["capex"]).all()


def test_point_in_time_hides_unpublished_rows():
    df = frame(QUARTERS)
    assert len(point_in_time(df, date(2026, 2, 9))) == 3
    assert len(point_in_time(df, date(2026, 2, 10))) == 4


def test_ttm_sums_four_consecutive_quarters():
    t = ttm(frame(QUARTERS), date(2026, 3, 1))
    assert t["revenue"] == 46
    assert t["total_equity"] == 120  # latest balance sheet
    assert t["flow_period_end"] == pd.Timestamp("2025-12-31")


def test_ttm_falls_back_to_annual_when_quarters_incomplete():
    rows = [
        *QUARTERS[:3],
        dict(period_type="A", period_end="2025-03-31", available_from="2025-05-30", revenue=40),
    ]
    t = ttm(frame(rows), date(2026, 1, 1))
    assert t["revenue"] == 40  # only 3 quarters known


def test_ttm_prefers_fresher_annual_over_older_quarters():
    rows = [
        *QUARTERS,
        dict(period_type="A", period_end="2025-12-31", available_from="2026-02-10", revenue=99),
    ]
    assert ttm(frame(rows), date(2026, 3, 1))["revenue"] == 99


def test_ttm_missing_quarter_value_is_nan_not_undercount():
    rows = [dict(r) for r in QUARTERS]
    rows[1]["revenue"] = None
    assert np.isnan(ttm(frame(rows), date(2026, 3, 1))["revenue"])


def test_ttm_none_before_any_data():
    assert ttm(frame(QUARTERS), date(2020, 1, 1)) is None


def test_ttm_rejects_gapped_quarters():
    rows = [QUARTERS[0], QUARTERS[1], QUARTERS[3], quarter("2026-03-31", "2026-05-10", 14)]
    assert ttm(frame(rows), date(2026, 6, 1)) is None


@pytest.mark.parametrize("dup_first", [True, False])
def test_normalize_drops_duplicate_periods(dup_first):
    rows = [QUARTERS[0], {**QUARTERS[0], "revenue": 999}]
    assert len(frame(rows if dup_first else rows[::-1])) == 1
