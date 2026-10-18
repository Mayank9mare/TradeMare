"""Corporate events: official announcements and the next results date.

- India: NSE corporate announcements and board-meeting notices (the exchange's own filings).
  The upcoming results date comes from board meetings called to approve results, with Yahoo as
  a fallback.
- India, BSE-only filers (e.g. MCX): BSE's data API refuses automated clients, so when NSE has no
  filings for a company its last 90 days of news headlines are scanned instead (source "News"),
  and the red-flag rules treat those as cautions only.
- US: SEC 8-K filings (with their item codes) and late-filing notices, plus Yahoo's next earnings
  date.

Events are fetched only for symbols that matter on a given day (entry candidates and holdings),
cached for ``max_age_hours`` and never raise: on failure the result carries an ``error`` and the
caller decides what to do. NSE's public site is not an official API: requests are spaced out and
kept to a few dozen a day.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from trademare.core.models import Market

log = logging.getLogger(__name__)

NSE_HOME = "https://www.nseindia.com/"
NSE_ANNOUNCEMENTS = "https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={symbol}"
NSE_BOARD_MEETINGS = "https://www.nseindia.com/api/corporate-board-meetings?index=equities&symbol={symbol}"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_DOCUMENT = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": NSE_HOME,
}
_RESULTS = re.compile(r"financial result|results", re.IGNORECASE)


@dataclass
class Announcement:
    day: date
    category: str
    text: str
    source: str  # "NSE" | "SEC" | "News"
    url: str | None = None
    items: list[str] = field(default_factory=list)  # SEC 8-K item codes, e.g. ["4.02", "9.01"]


@dataclass
class CorporateEvents:
    market: str
    symbol: str
    fetched_at: datetime
    next_results: date | None = None
    results_source: str | None = None
    announcements: list[Announcement] = field(default_factory=list)
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["fetched_at"] = self.fetched_at.isoformat()
        d["next_results"] = self.next_results.isoformat() if self.next_results else None
        for a in d["announcements"]:
            a["day"] = a["day"].isoformat()
        return d

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> CorporateEvents:
        anns = [
            Announcement(**(a | {"day": date.fromisoformat(a["day"])})) for a in d.get("announcements", [])
        ]
        return cls(
            market=d["market"],
            symbol=d["symbol"],
            fetched_at=datetime.fromisoformat(d["fetched_at"]),
            next_results=date.fromisoformat(d["next_results"]) if d.get("next_results") else None,
            results_source=d.get("results_source"),
            announcements=anns,
            error=d.get("error"),
        )


def _nse_day(text: str) -> date | None:
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


class NseClient:
    def __init__(self, session: requests.Session | None = None, pause: float = 1.0) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(BROWSER_HEADERS)
        self.pause = pause
        self._last = 0.0
        self._warmed = False

    def get_json(self, url: str) -> Any:
        if not self._warmed:  # the home page sets cookies some API routes want; a 403 there is normal
            with contextlib.suppress(requests.RequestException):
                self.session.get(NSE_HOME, timeout=15)
            self._warmed = True
        for attempt in range(3):
            wait = self.pause - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            r = self.session.get(url, timeout=20)
            if r.status_code == 200:
                return r.json()
            time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"NSE returned HTTP {r.status_code}")

    def announcements(self, symbol: str, since: date) -> list[Announcement]:
        out = []
        for a in self.get_json(NSE_ANNOUNCEMENTS.format(symbol=requests.utils.quote(symbol))):
            day = _nse_day(a.get("an_dt") or "")
            if day is None or day < since:
                continue
            out.append(
                Announcement(
                    day=day,
                    category=(a.get("desc") or "").strip(),
                    text=(a.get("attchmntText") or "").strip(),
                    source="NSE",
                    url=a.get("attchmntFile") or None,
                )
            )
        return sorted(out, key=lambda a: a.day, reverse=True)

    def next_results(self, symbol: str, today: date) -> date | None:
        dates = []
        for m in self.get_json(NSE_BOARD_MEETINGS.format(symbol=requests.utils.quote(symbol))):
            day = _nse_day(m.get("bm_date") or "")
            purpose = f"{m.get('bm_purpose') or ''} {m.get('bm_desc') or ''}"
            if day and day >= today and _RESULTS.search(purpose):
                dates.append(day)
        return min(dates) if dates else None


# 8-K items worth surfacing (all others are routine).
SEC_ITEMS = {
    "1.03": "Bankruptcy or receivership",
    "2.04": "Debt acceleration or triggering event",
    "2.05": "Exit or restructuring costs",
    "2.06": "Material impairment",
    "3.01": "Delisting or listing-rule notice",
    "4.01": "Change of auditor",
    "4.02": "Prior financial statements can no longer be relied on",
    "5.02": "Director or officer departure / appointment",
    "2.02": "Results of operations",
}


def sec_announcements(payload: dict[str, Any], cik: str, since: date) -> list[Announcement]:
    recent = payload.get("filings", {}).get("recent", {})
    out = []
    keys = ("form", "filingDate", "items", "accessionNumber", "primaryDocument")
    for form, filed, items, accession, doc in zip(*(recent.get(k, []) for k in keys), strict=False):
        day = date.fromisoformat(filed)
        if day < since:
            break  # newest first
        codes = [c.strip() for c in (items or "").split(",") if c.strip()]
        if form in ("8-K", "8-K/A"):
            notable = [c for c in codes if c in SEC_ITEMS]
            if not notable:
                continue
            text = "; ".join(f"{c} {SEC_ITEMS[c]}" for c in notable)
            category = form
        elif form in ("NT 10-K", "NT 10-Q"):
            text, category = "Notice of late filing of the periodic report", form
        else:
            continue
        url = SEC_DOCUMENT.format(cik=int(cik), accession=accession.replace("-", ""), document=doc)
        out.append(Announcement(day=day, category=category, text=text, source="SEC", url=url, items=codes))
    return out


def yahoo_next_earnings(market: Market, symbol: str, today: date) -> date | None:
    import yfinance as yf

    from trademare.data.providers.yfinance_provider import yf_symbol

    cal = yf.Ticker(yf_symbol(market, symbol)).calendar
    raw = cal.get("Earnings Date") if isinstance(cal, dict) else None
    upcoming = [d for d in (raw or []) if isinstance(d, date) and d >= today]
    return min(upcoming) if upcoming else None


NEWS_LOOKBACK_DAYS = 90
NEWS_MAX_ITEMS = 40


def news_announcements(market: Market, company: str) -> list[Announcement]:
    from trademare.analysis.news import fetch_headlines

    return [
        Announcement(
            day=h.published.date(), category="News headline", text=f"{h.title} ({h.source})", source="News"
        )
        for h in fetch_headlines(market, company, NEWS_LOOKBACK_DAYS, NEWS_MAX_ITEMS)
    ]


class EventsService:
    def __init__(
        self,
        cache_dir: str | Path,
        sec_client=None,
        ciks: dict[str, str] | None = None,
        nse: NseClient | None = None,
        lookback_days: int = 180,
        max_age_hours: float = 20,
        names: dict[str, str] | None = None,
    ) -> None:
        self.root = Path(cache_dir) / "events"
        self.sec_client, self.ciks = sec_client, ciks or {}
        self.names = names or {}  # India symbol -> company name, for the news fallback
        self._nse = nse
        self.lookback_days, self.max_age = lookback_days, timedelta(hours=max_age_hours)

    @property
    def nse(self) -> NseClient:
        if self._nse is None:
            self._nse = NseClient()
        return self._nse

    def _path(self, market: Market, symbol: str) -> Path:
        return self.root / market.value / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', symbol)}.json"

    def cached(self, market: Market, symbol: str) -> CorporateEvents | None:
        path = self._path(market, symbol)
        if not path.exists():
            return None
        try:
            return CorporateEvents.from_json(json.loads(path.read_text()))
        except (ValueError, KeyError, TypeError):
            return None

    def get(
        self, market: Market, symbol: str, today: date | None = None, refresh: bool = False
    ) -> CorporateEvents:
        today = today or date.today()
        hit = self.cached(market, symbol)
        if hit and not refresh and not hit.error and datetime.now(UTC) - hit.fetched_at < self.max_age:
            return hit
        events = self._fetch(market, symbol, today)
        if events.error and hit and not hit.error:
            hit.error = f"stale: {events.error}"  # keep the last good copy, but say it is stale
            return hit
        path = self._path(market, symbol)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(events.to_json(), indent=1))
        return events

    def _fetch(self, market: Market, symbol: str, today: date) -> CorporateEvents:
        ev = CorporateEvents(market.value, symbol, datetime.now(UTC))
        since = today - timedelta(days=self.lookback_days)
        errors = []
        try:
            if market is Market.IN:
                ev.announcements = self.nse.announcements(symbol, since)
                ev.next_results = self.nse.next_results(symbol, today)
                ev.results_source = "NSE board meeting" if ev.next_results else None
                if not ev.announcements and symbol in self.names:  # files with BSE only
                    try:
                        ev.announcements = news_announcements(market, self.names[symbol])
                    except Exception as e:
                        errors.append(f"news fallback: {type(e).__name__}: {e}")
            else:
                cik = self.ciks.get(symbol)
                if cik and self.sec_client is not None:
                    payload = self.sec_client.get_json(SEC_SUBMISSIONS.format(cik=str(cik).zfill(10)))
                    ev.announcements = sec_announcements(payload, str(cik), since)
                elif not cik:
                    errors.append("no SEC CIK for symbol")
        except Exception as e:  # network, blocking, format changes: report, never raise
            errors.append(f"{type(e).__name__}: {e}")
        if ev.next_results is None:
            try:
                ev.next_results = yahoo_next_earnings(market, symbol, today)
                ev.results_source = "Yahoo" if ev.next_results else None
            except Exception as e:
                errors.append(f"earnings date: {type(e).__name__}: {e}")
        ev.error = "; ".join(errors) or None
        if ev.error:
            log.warning("%s %s events: %s", market, symbol, ev.error)
        return ev
