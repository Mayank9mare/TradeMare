from datetime import UTC, date, datetime

import pytest

from trademare.core.clock import MarketClock
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


def test_out_of_range_date_has_clear_error():
    with pytest.raises(ValueError, match="outside the IN calendar range"):
        MarketClock(Market.IN).is_session(date(2040, 1, 1))


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
