from datetime import UTC, date, datetime

import pytest

from trademare.core.clock import MarketClock, load_holidays
from trademare.core.models import Market


def test_us_holiday_and_weekend():
    clock = MarketClock(Market.US)
    assert not clock.is_session(date(2025, 12, 25))  # Christmas
    assert not clock.is_session(date(2025, 10, 11))  # Saturday
    assert clock.is_session(date(2025, 10, 10))
    assert clock.next_session(date(2025, 10, 10)) == date(2025, 10, 13)
    assert clock.previous_session(date(2025, 10, 13)) == date(2025, 10, 10)
    assert clock.next_session(date(2025, 10, 11)) == date(2025, 10, 13)


def test_india_holiday_and_close_time():
    clock = MarketClock(Market.IN)
    assert not clock.is_session(date(2025, 10, 2))  # Gandhi Jayanti
    close = clock.session_close(date(2025, 10, 1))
    assert (close.hour, close.minute) == (15, 30)
    assert str(close.tzinfo) == "Asia/Kolkata"


def test_is_open_during_session():
    clock = MarketClock(Market.US)
    assert clock.is_open(datetime(2025, 10, 10, 15, 0, tzinfo=UTC))  # 11:00 New York
    assert not clock.is_open(datetime(2025, 10, 11, 15, 0, tzinfo=UTC))  # Saturday


def test_before_calendar_range_has_clear_error():
    with pytest.raises(ValueError, match="before the IN calendar"):
        MarketClock(Market.IN).is_session(date(1990, 1, 1))


def test_beyond_calendar_range_uses_weekdays_minus_holiday_file(tmp_path, caplog):
    holidays_dir = tmp_path / "holidays"
    holidays_dir.mkdir()
    (holidays_dir / "IN.txt").write_text("# comment\n2027-01-26  # Republic Day\n")
    clock = MarketClock(Market.IN, holidays=load_holidays(Market.IN, holidays_dir))
    assert clock.last == date(2026, 12, 31)  # update this test when the library extends its range
    assert clock.is_session(date(2027, 1, 25))  # Monday
    assert not clock.is_session(date(2027, 1, 26))  # holiday from the file
    assert not clock.is_session(date(2027, 1, 30))  # Saturday
    assert clock.next_session(date(2027, 1, 25)) == date(2027, 1, 27)
    # Crossing the boundary works in both directions.
    assert clock.next_session(date(2026, 12, 31)) == date(2027, 1, 1)
    assert clock.previous_session(date(2027, 1, 1)) == date(2026, 12, 31)
    close = clock.session_close(date(2027, 1, 25))
    assert (close.hour, close.minute, str(close.tzinfo)) == (15, 30, "Asia/Kolkata")
    assert clock.is_open(datetime(2027, 1, 25, 6, 0, tzinfo=UTC))  # 11:30 IST
    assert "past the holiday calendar" in caplog.text


def test_last_completed_session():
    clock = MarketClock(Market.US)
    # Friday 2025-10-10: before close -> Thursday; an hour after close -> Friday
    assert clock.last_completed_session(datetime(2025, 10, 10, 18, 0, tzinfo=UTC)) == date(2025, 10, 9)
    assert clock.last_completed_session(datetime(2025, 10, 10, 21, 30, tzinfo=UTC)) == date(2025, 10, 10)
    # Weekend -> Friday
    assert clock.last_completed_session(datetime(2025, 10, 12, 12, 0, tzinfo=UTC)) == date(2025, 10, 10)
    india = MarketClock(Market.IN)
    # 17:00 IST is after 15:30 close + 60 min
    assert india.last_completed_session(datetime(2025, 10, 1, 11, 30, tzinfo=UTC)) == date(2025, 10, 1)
