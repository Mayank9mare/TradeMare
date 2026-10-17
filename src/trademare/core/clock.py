"""Exchange trading calendars: sessions, holidays and close times per market.

Uses ``exchange_calendars`` for its covered range. Exchanges publish next
year's holidays only late in the year, so beyond the library's last session
the clock falls back to weekdays minus the dates in ``config/holidays/<M>.txt``
(one ISO date per line, ``#`` comments), with the market's regular hours, and
logs a warning so the file gets filled in.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo

import exchange_calendars as xc
import pandas as pd

from trademare.core.models import Market

log = logging.getLogger(__name__)

HOLIDAY_DIR = Path("config/holidays")
# Regular hours used beyond the library's range.
REGULAR_HOURS = {Market.IN: (time(9, 15), time(15, 30)), Market.US: (time(9, 30), time(16, 0))}
MAX_GAP_DAYS = 15  # no exchange is closed this long; guards the session search


@cache
def _calendar(code: str) -> xc.ExchangeCalendar:
    return xc.get_calendar(code)


def load_holidays(market: Market, directory: Path = HOLIDAY_DIR) -> frozenset[date]:
    path = directory / f"{market.value}.txt"
    if not path.exists():
        return frozenset()
    days = set()
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            days.add(date.fromisoformat(line))
    return frozenset(days)


class MarketClock:
    def __init__(self, market: Market, holidays: frozenset[date] | None = None) -> None:
        self.market = market
        self.calendar = _calendar(market.calendar_code)
        self.first = self.calendar.first_session.date()
        self.last = self.calendar.last_session.date()
        self._holidays = holidays
        self._warned = False

    @property
    def tz(self) -> str:
        return str(self.calendar.tz)

    @property
    def holidays(self) -> frozenset[date]:
        if self._holidays is None:
            self._holidays = load_holidays(self.market)
        return self._holidays

    def _fallback(self, day: date) -> bool:
        """True when ``day`` is past the library's range (raises before it)."""
        if day < self.first:
            raise ValueError(f"{day} is before the {self.market} calendar's first session {self.first}")
        if day <= self.last:
            return False
        if not self._warned:
            log.warning(
                "%s: %s is past the holiday calendar (ends %s); using weekdays minus %s — "
                "add the exchange's published holidays there",
                self.market, day, self.last, HOLIDAY_DIR / f"{self.market.value}.txt",
            )  # fmt: skip
            self._warned = True
        return True

    def is_session(self, day: date) -> bool:
        if self._fallback(day):
            return day.weekday() < 5 and day not in self.holidays
        return bool(self.calendar.is_session(pd.Timestamp(day)))

    def next_session(self, day: date) -> date:
        """First session strictly after ``day``."""
        for i in range(1, MAX_GAP_DAYS + 1):
            candidate = day + timedelta(days=i)
            if self.is_session(candidate):
                return candidate
        raise ValueError(f"no {self.market} session within {MAX_GAP_DAYS} days after {day}")

    def previous_session(self, day: date) -> date:
        """Last session strictly before ``day``."""
        for i in range(1, MAX_GAP_DAYS + 1):
            candidate = day - timedelta(days=i)
            if self.is_session(candidate):
                return candidate
        raise ValueError(f"no {self.market} session within {MAX_GAP_DAYS} days before {day}")

    def sessions_between(self, after: date, through: date) -> list[date]:
        """Sessions strictly after ``after`` up to and including ``through``."""
        days = (through - after).days
        return [d for i in range(1, days + 1) if self.is_session(d := after + timedelta(days=i))]

    def _regular(self, day: date, which: int) -> datetime:
        return datetime.combine(day, REGULAR_HOURS[self.market][which], ZoneInfo(self.tz))

    def session_close(self, day: date) -> datetime:
        """Close time of the session on ``day`` (timezone-aware, market tz)."""
        if self._fallback(day):
            return self._regular(day, 1)
        return self.calendar.session_close(pd.Timestamp(day)).tz_convert(self.tz).to_pydatetime()

    def session_open(self, day: date) -> datetime:
        if self._fallback(day):
            return self._regular(day, 0)
        return self.calendar.session_open(pd.Timestamp(day)).tz_convert(self.tz).to_pydatetime()

    def last_completed_session(self, at: datetime | None = None, settle_minutes: int = 60) -> date:
        """Most recent session whose daily bar should be final at ``at``.

        Today's session counts once ``settle_minutes`` have passed after the
        close, giving data vendors time to publish the final bar.
        """
        at_ts = pd.Timestamp(at) if at else pd.Timestamp.now(tz="UTC")
        if at_ts.tzinfo is None:
            raise ValueError("last_completed_session requires a timezone-aware datetime")
        local_day = at_ts.tz_convert(self.tz).date()
        if self.is_session(local_day):
            settled = pd.Timestamp(self.session_close(local_day)) + pd.Timedelta(minutes=settle_minutes)
            if at_ts >= settled:
                return local_day
        return self.previous_session(local_day)

    def now(self) -> datetime:
        return pd.Timestamp.now(tz=self.tz).to_pydatetime()

    def is_open(self, at: datetime | None = None) -> bool:
        at_ts = pd.Timestamp(at) if at else pd.Timestamp.now(tz="UTC")
        if at_ts.tzinfo is None:
            raise ValueError("is_open requires a timezone-aware datetime")
        day = at_ts.tz_convert(self.tz).date()
        if self._fallback(day):
            return self.is_session(day) and self.session_open(
                day
            ) <= at_ts.to_pydatetime() < self.session_close(day)
        return bool(self.calendar.is_open_on_minute(at_ts))
