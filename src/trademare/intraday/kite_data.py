"""5-minute candles from Kite Connect's historical API (needs the paid Connect plan).

Uses the owner's daily Zerodha login (data/tokens/zerodha.json, read only) and the API key in .env.
Requests are chunked (Kite serves at most 100 days of minute candles per call) and paced under
Kite's limit of 3 historical requests a second. Times are returned in IST without a timezone.
"""

from __future__ import annotations

import json
import time as clock
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from trademare.intraday.store import COLUMNS

CHUNK_DAYS = {"minute": 60, "3minute": 100, "5minute": 100, "10minute": 100, "15minute": 200}
PACE_SECONDS = 0.36


class KiteDataError(RuntimeError):
    pass


def kite_client() -> Any:
    """A KiteConnect client with today's saved login (no login flow here: run
    ``trademare broker login zerodha`` first)."""
    from kiteconnect import KiteConnect

    from trademare.config.secrets import Secrets
    from trademare.execution.brokers.tokens import load_token
    from trademare.execution.brokers.zerodha import session_active

    secrets = Secrets()
    if not secrets.zerodha_api_key:
        raise KiteDataError("ZERODHA_API_KEY is missing in .env")
    stored = load_token("zerodha")
    if stored is None or not session_active():
        raise KiteDataError("no Zerodha login today: run `uv run trademare broker login zerodha`")
    kite = KiteConnect(api_key=secrets.zerodha_api_key.get_secret_value())
    kite.set_access_token(stored[0])
    return kite


class KiteCandles:
    def __init__(self, kite: Any, cache_dir: Path, sleep: Callable[[float], None] = clock.sleep) -> None:
        self.kite = kite
        self.cache_dir = Path(cache_dir)
        self.sleep = sleep
        self._tokens: dict[str, int] | None = None

    def tokens(self, refresh: bool = False) -> dict[str, int]:
        """NSE trading symbol -> Kite instrument token (cached for the day)."""
        path = self.cache_dir / "instruments-NSE.json"
        if self._tokens is not None and not refresh:
            return self._tokens
        if path.exists() and not refresh:
            saved = json.loads(path.read_text())
            if saved.get("date") == date.today().isoformat():
                self._tokens = saved["tokens"]
                return self._tokens
        rows = self.kite.instruments("NSE")
        tokens = {
            r["tradingsymbol"]: int(r["instrument_token"])
            for r in rows
            if r.get("segment") == "NSE" and r.get("instrument_type") == "EQ"
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"date": date.today().isoformat(), "tokens": tokens}))
        self._tokens = tokens
        return tokens

    def fetch(self, symbol: str, start: datetime, end: datetime, interval: str = "5minute") -> pd.DataFrame:
        token = self.tokens().get(symbol)
        if token is None:
            raise KiteDataError(f"{symbol} is not an NSE equity on Kite")
        step = timedelta(days=CHUNK_DAYS.get(interval, 100))
        frames = []
        cursor = start
        while cursor <= end:
            stop = min(cursor + step - timedelta(minutes=1), end)
            try:
                rows = self.kite.historical_data(token, cursor, stop, interval)
            except Exception as e:  # kiteconnect raises its own exception classes
                raise KiteDataError(f"{symbol} {cursor:%Y-%m-%d}: {e}") from e
            if rows:
                frames.append(pd.DataFrame(rows))
            self.sleep(PACE_SECONDS)
            cursor = stop + timedelta(minutes=1)
        if not frames:
            return pd.DataFrame(columns=COLUMNS, index=pd.DatetimeIndex([], name="time"))
        df = pd.concat(frames)
        times = pd.to_datetime(df["date"])
        if times.dt.tz is not None:
            times = times.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        df.index = pd.DatetimeIndex(times, name="time")
        df = df[COLUMNS].astype(float)
        return df[~df.index.duplicated(keep="last")].sort_index()
