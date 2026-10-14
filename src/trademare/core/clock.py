"""Exchange trading calendars: sessions, holidays and close times per market."""

from __future__ import annotations

from datetime import date, datetime
from functools import cache

import exchange_calendars as xc
import pandas as pd

from trademare.core.models import Market


@cache
def _calendar(code: str) -> xc.ExchangeCalendar:
    return xc.get_calendar(code)


class MarketClock:
    def __init__(self, market: Market) -> None:
        self.market = market
        self.calendar = _calendar(market.calendar_code)

    @property
    def tz(self) -> str:
        return str(self.calendar.tz)

    def _check_range(self, day: date) -> pd.Timestamp:
        ts = pd.Timestamp(day)
        first, last = self.calendar.first_session, self.calendar.last_session
        if not first <= ts <= last:
            raise ValueError(
                f"{day} is outside the {self.market} calendar range {first.date()}..{last.date()}; "
                "upgrade exchange-calendars for newer holiday data"
            )
        return ts

    def is_session(self, day: date) -> bool:
        return bool(self.calendar.is_session(self._check_range(day)))

    def next_session(self, day: date) -> date:
        """First session strictly after ``day``."""
        ts = self._check_range(day)
        if self.calendar.is_session(ts):
            return self.calendar.next_session(ts).date()
        return self.calendar.date_to_session(ts, direction="next").date()

    def previous_session(self, day: date) -> date:
        """Last session strictly before ``day``."""
        ts = self._check_range(day)
        if self.calendar.is_session(ts):
            return self.calendar.previous_session(ts).date()
        return self.calendar.date_to_session(ts, direction="previous").date()

    def session_close(self, day: date) -> datetime:
        """Close time of the session on ``day`` (timezone-aware, market tz)."""
        ts = self._check_range(day)
        return self.calendar.session_close(ts).tz_convert(self.tz).to_pydatetime()

    def session_open(self, day: date) -> datetime:
        ts = self._check_range(day)
        return self.calendar.session_open(ts).tz_convert(self.tz).to_pydatetime()

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
        return bool(self.calendar.is_open_on_minute(at_ts))
