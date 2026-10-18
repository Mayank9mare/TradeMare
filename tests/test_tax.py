from datetime import date

from tests.test_desk import desk  # noqa: F401  (fixture)
from trademare.config.schema import TaxConfig
from trademare.risk.tax import long_term_from, tax_hint
from trademare.storage.db import PositionRow

INDIA = TaxConfig(warn_within_days=60, short_term_rate_pct=20, long_term_rate_pct=12.5)


def test_long_term_starts_the_day_after_the_anniversary():
    assert long_term_from(date(2025, 10, 22)) == date(2026, 10, 23)
    assert long_term_from(date(2024, 2, 29)) == date(2025, 3, 2)


def test_hint_with_saving_estimate():
    h = tax_hint(INDIA, date(2025, 11, 20), date(2026, 10, 19), gain=10_000, currency="₹")
    assert h.long_term_on == date(2026, 11, 21) and h.days_left == 33
    assert h.saving == 750 and "saves about ₹750" in h.text


def test_no_hint_for_losses_shorts_far_off_or_already_long_term():
    entry, today = date(2025, 11, 20), date(2026, 10, 19)
    assert tax_hint(INDIA, entry, today, gain=-500) is None
    assert tax_hint(INDIA, entry, today, gain=500, long=False) is None
    assert tax_hint(INDIA, date(2026, 6, 1), today, gain=500) is None  # 7 months to go
    assert tax_hint(INDIA, date(2025, 6, 1), today, gain=500) is None  # already long-term
    assert tax_hint(INDIA.model_copy(update={"warn_within_days": 0}), entry, today, gain=500) is None


def test_us_without_rates_gives_days_only():
    h = tax_hint(TaxConfig(), date(2025, 11, 20), date(2026, 10, 19), gain=900, currency="$")
    assert h.saving is None and "33 days" in h.text and "saves" not in h.text


def test_exit_signal_carries_the_hint(desk):  # noqa: F811
    pos = PositionRow(
        symbol="X", direction="LONG", quantity=10, entry_price=100.0, last_price=150.0,
        entry_date=date(2025, 11, 20),
    )  # fmt: skip
    reasons = desk._exit_reasons(pos, date(2026, 10, 19), "rank fell", 0.1)
    assert reasons["tax"][0].startswith("Tax:") and "₹38" in reasons["tax"][0]  # 500 x 7.5%
    pos.entry_date = date(2026, 5, 1)
    assert "tax" not in desk._exit_reasons(pos, date(2026, 10, 19), "rank fell", 0.1)
