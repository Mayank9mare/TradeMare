"""IPOs: the NSE mainboard calendar with live subscription, and the US calendar from Nasdaq.

India (NSE's public site, the same pages a browser loads; not an official API):
- ``all-upcoming-issues``: issues open now or opening soon.
- ``public-past-issues``: closed issues with their final price and listing date.
- ``ipo-detail``: issue facts (lot size, fresh issue or offer for sale, lead managers, prospectus).
- ``ipo-active-category``: subscription by investor category, NSE and BSE combined, updated
  hourly while an issue is open.

US: Nasdaq's IPO calendar (upcoming and priced deals). US issues publish no demand figures, so
they are listed for information only.

Everything is cached under ``data/cache/ipo``. Final figures (an issue that has closed) never
change and are fetched once; open issues refresh hourly. Requests reuse the spaced-out
``NseClient``. Nothing here raises on network failure: callers get what the cache has.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from trademare.data.events import BROWSER_HEADERS, NseClient

log = logging.getLogger(__name__)

NSE_UPCOMING = "https://www.nseindia.com/api/all-upcoming-issues?category=ipo"
NSE_PAST = "https://www.nseindia.com/api/public-past-issues"
NSE_DETAIL = "https://www.nseindia.com/api/ipo-detail?symbol={symbol}&series=EQ"
NSE_SUBSCRIPTION = "https://www.nseindia.com/api/ipo-active-category?symbol={symbol}"
NASDAQ_CALENDAR = "https://api.nasdaq.com/api/ipo/calendar?date={month}"
IST = ZoneInfo("Asia/Kolkata")
OPEN_MAX_AGE_S = 3600
AWAIT_LISTING_DAYS = 10
CALENDAR_MAX_AGE_S = 6 * 3600


@dataclass
class Subscription:
    """Times each portion was bid for (2.0 = bids for twice the shares on offer)."""

    qib: float | None = None
    nii: float | None = None
    retail: float | None = None
    employee: float | None = None
    total: float | None = None
    as_of: str = ""


@dataclass
class IssueFacts:
    lot: int | None = None
    kind: str = ""  # "fresh", "ofs" (offer for sale) or "fresh+ofs"
    issue_text: str = ""
    lead_managers: str = ""
    registrar: str = ""
    prospectus_url: str = ""
    face_value: str = ""


@dataclass
class Ipo:
    market: str
    symbol: str
    company: str
    start: date | None
    end: date | None
    price_low: float | None
    price_high: float | None
    shares_offered: float | None = None
    issue_price: float | None = None  # the final price, once fixed
    listing_date: date | None = None
    series: str = "EQ"
    exchange: str = ""
    facts: IssueFacts | None = None
    subscription: Subscription | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def status(self, today: date) -> str:
        """upcoming, open, closed (awaiting listing) or listed."""
        if self.listing_date and self.listing_date <= today:
            return "listed"
        if self.start and today < self.start:
            return "upcoming"
        if self.end and today > self.end:
            return "closed"
        return "open" if self.start else "upcoming"

    @property
    def price(self) -> float | None:
        """The price an applicant pays: the final price or the top of the band (the cut-off)."""
        return self.issue_price or self.price_high

    @property
    def issue_value(self) -> float | None:
        if self.shares_offered and self.price:
            return self.shares_offered * self.price
        return None

    @property
    def min_investment(self) -> float | None:
        lot = self.facts.lot if self.facts else None
        return lot * self.price if lot and self.price else None


def ist_now() -> datetime:
    return datetime.now(IST)


# ---------- parsing ----------

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _float(text: Any) -> float | None:
    if text is None:
        return None
    try:
        return float(str(text).replace(",", "").strip())
    except ValueError:
        return None


def price_band(text: str | None) -> tuple[float | None, float | None]:
    """'Rs.558 to Rs.586' -> (558, 586); 'Rs.1000' -> (1000, 1000)."""
    nums = [float(n.replace(",", "")) for n in _NUM.findall(text or "")]
    if not nums:
        return None, None
    return min(nums), max(nums)


def nse_date(text: str | None) -> date | None:
    for fmt in ("%d-%b-%Y", "%d-%B-%Y"):
        try:
            return datetime.strptime((text or "").strip().title(), fmt).date()
        except ValueError:
            continue
    return None


def issue_kind(text: str) -> str:
    t = text.lower()
    fresh, ofs = "fresh issue" in t, "offer for sale" in t
    return "fresh+ofs" if fresh and ofs else "fresh" if fresh else "ofs" if ofs else ""


def parse_facts(detail: dict[str, Any]) -> IssueFacts:
    rows = {
        str(r.get("title") or "").strip(): str(r.get("value") or "").strip().strip('"')
        for r in (detail.get("issueInfo") or {}).get("dataList", [])
    }
    lot = _NUM.search(rows.get("Bid Lot", ""))
    issue_text = rows.get("Issue Size", "")
    return IssueFacts(
        lot=int(lot.group().replace(",", "")) if lot else None,
        kind=issue_kind(issue_text),
        issue_text=issue_text,
        lead_managers=rows.get("Book Running Lead Managers", ""),
        registrar=rows.get("Name of the Registrar", ""),
        prospectus_url=rows.get("Red Herring Prospectus", ""),
        face_value=rows.get("Face Value", ""),
    )


_CATEGORIES = {"1": "qib", "2": "nii", "3": "retail", "4": "employee"}


def parse_subscription(payload: dict[str, Any]) -> Subscription | None:
    sub = Subscription(as_of=str(payload.get("updateTime") or "").replace("Updated as on", "").strip())
    found = False
    for r in payload.get("dataList", []):
        times = _float(r.get("noOfTotalMeant"))
        if times is None:
            continue
        sr = r.get("srNo")
        if sr is None and str(r.get("category", "")).strip().lower() == "total":
            sub.total, found = times, True
        elif sr in _CATEGORIES:
            setattr(sub, _CATEGORIES[sr], times)
            found = True
    return sub if found else None


def parse_upcoming(rows: list[dict[str, Any]]) -> list[Ipo]:
    out = []
    for r in rows:
        low, high = price_band(r.get("issuePrice"))
        out.append(
            Ipo(
                market="IN",
                symbol=str(r.get("symbol", "")).strip(),
                company=str(r.get("companyName", "")).strip(),
                start=nse_date(r.get("issueStartDate")),
                end=nse_date(r.get("issueEndDate")),
                price_low=low,
                price_high=high,
                shares_offered=_float(r.get("issueSize")),
                series=str(r.get("series") or "EQ"),
                exchange="NSE",
            )
        )
    return [i for i in out if i.symbol]


def parse_past(rows: list[dict[str, Any]], series: tuple[str, ...] = ("EQ",)) -> list[Ipo]:
    out = []
    for r in rows:
        if str(r.get("securityType", "")).strip() not in series:
            continue
        low, high = price_band(r.get("priceRange"))
        out.append(
            Ipo(
                market="IN",
                symbol=str(r.get("symbol", "")).strip(),
                company=str(r.get("company") or r.get("companyName") or "").strip(),
                start=nse_date(r.get("ipoStartDate")),
                end=nse_date(r.get("ipoEndDate")),
                price_low=low,
                price_high=high,
                issue_price=_float(r.get("issuePrice")),
                listing_date=nse_date(r.get("listingDate")),
                series=str(r.get("securityType", "")).strip(),
                exchange="NSE",
            )
        )
    return [i for i in out if i.symbol]


def parse_nasdaq(payload: dict[str, Any]) -> list[Ipo]:
    """Upcoming and recently priced US deals from Nasdaq's calendar."""
    data = payload.get("data") or {}
    out = []
    upcoming = (data.get("upcoming") or {}).get("upcomingTable")
    sections = (("upcoming", upcoming), ("priced", data.get("priced")))
    for status, table in sections:
        for r in (table or {}).get("rows") or []:
            low, high = price_band(r.get("proposedSharePrice"))
            when = r.get("expectedPriceDate") or r.get("pricedDate")
            try:
                day = datetime.strptime(str(when), "%m/%d/%Y").date()
            except ValueError:
                day = None
            out.append(
                Ipo(
                    market="US",
                    symbol=str(r.get("proposedTickerSymbol") or "").strip(),
                    company=str(r.get("companyName") or "").strip().title(),
                    start=day,
                    end=day,
                    price_low=low,
                    price_high=high,
                    shares_offered=_float(r.get("sharesOffered")),
                    issue_price=high if status == "priced" else None,
                    listing_date=day if status == "priced" else None,
                    exchange=str(r.get("proposedExchange") or ""),
                    extra={"deal": status},
                )
            )
    return [i for i in out if i.symbol]


# ---------- cached service ----------


def _to_json(ipo: Ipo) -> dict[str, Any]:
    return json.loads(json.dumps(asdict(ipo), default=str))


def _day(v: Any) -> date | None:
    return date.fromisoformat(v) if v else None


def _from_json(d: dict[str, Any]) -> Ipo:
    d = dict(d)
    for k in ("start", "end", "listing_date"):
        d[k] = _day(d.get(k))
    d["facts"] = IssueFacts(**d["facts"]) if d.get("facts") else None
    d["subscription"] = Subscription(**d["subscription"]) if d.get("subscription") else None
    return Ipo(**d)


class IpoService:
    def __init__(self, cache_dir: str | Path, nse: NseClient | None = None, session=None) -> None:
        self.dir = Path(cache_dir) / "ipo"
        self._nse = nse
        self._session = session

    @property
    def nse(self) -> NseClient:
        if self._nse is None:
            self._nse = NseClient(pause=1.5)
        return self._nse

    # --- tiny json cache ---
    def _read(self, name: str, max_age: float | None) -> Any:
        path = self.dir / name
        try:
            if max_age is not None and time.time() - path.stat().st_mtime > max_age:
                return None
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return None

    def _write(self, name: str, payload: Any) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / name).write_text(json.dumps(payload, default=str))

    def _cached_fetch(self, name: str, url: str, max_age: float | None, fetch=None) -> Any:
        fresh = self._read(name, max_age)
        if fresh is not None:
            return fresh
        try:
            payload = (fetch or self.nse.get_json)(url)
        except Exception as e:  # network, HTTP or JSON: fall back to whatever is cached
            log.warning("IPO fetch failed for %s: %s", url, e)
            return self._read(name, None)
        self._write(name, payload)
        return payload

    # --- India ---
    def upcoming(self) -> list[Ipo]:
        return parse_upcoming(self._cached_fetch("nse-upcoming.json", NSE_UPCOMING, OPEN_MAX_AGE_S) or [])

    def past(self, max_age: float = CALENDAR_MAX_AGE_S) -> list[Ipo]:
        return parse_past(self._cached_fetch("nse-past.json", NSE_PAST, max_age) or [])

    def facts(self, symbol: str) -> IssueFacts | None:
        payload = self._cached_fetch(f"detail-{symbol}.json", NSE_DETAIL.format(symbol=symbol), None)
        return parse_facts(payload) if isinstance(payload, dict) else None

    def subscription(self, symbol: str, final: bool) -> Subscription | None:
        url = NSE_SUBSCRIPTION.format(symbol=requests.utils.quote(symbol))
        payload = self._cached_fetch(f"sub-{symbol}.json", url, None if final else OPEN_MAX_AGE_S)
        return parse_subscription(payload) if isinstance(payload, dict) else None

    def india(self, today: date, recent_days: int = 30, details: bool = True) -> list[Ipo]:
        """Open and upcoming issues, plus those that closed in the last ``recent_days``."""
        by_symbol: dict[str, Ipo] = {}
        for ipo in self.past():
            if not ipo.end or not 0 <= (today - ipo.end).days <= recent_days:
                continue
            # mainboard issues list within a week; an older one with no listing date was withdrawn
            # or NSE never filled it in, so it is not shown as "awaiting listing"
            if ipo.listing_date or (today - ipo.end).days <= AWAIT_LISTING_DAYS:
                by_symbol[ipo.symbol] = ipo
        for ipo in self.upcoming():
            if ipo.series != "EQ":
                continue
            old = by_symbol.get(ipo.symbol)
            if old:  # the past list knows the final price and listing date
                ipo.issue_price, ipo.listing_date = old.issue_price, old.listing_date
            by_symbol[ipo.symbol] = ipo
        ipos = sorted(by_symbol.values(), key=lambda i: i.start or today, reverse=True)
        if details:
            for ipo in ipos:
                ipo.facts = self.facts(ipo.symbol)
                status = ipo.status(today)
                if status != "upcoming":  # no bids before an issue opens
                    ipo.subscription = self.subscription(ipo.symbol, final=status != "open")
        return ipos

    # --- US ---
    def us(self, today: date) -> list[Ipo]:
        def fetch(url: str) -> Any:
            s = self._session or requests.Session()
            r = s.get(url, headers={**BROWSER_HEADERS, "Referer": "https://www.nasdaq.com/"}, timeout=20)
            r.raise_for_status()
            return r.json()

        month = today.strftime("%Y-%m")
        payload = self._cached_fetch(
            f"nasdaq-{month}.json", NASDAQ_CALENDAR.format(month=month), CALENDAR_MAX_AGE_S, fetch
        )
        return parse_nasdaq(payload or {})


def first_and_last(bars, listing_date: date) -> dict[str, float] | None:
    """Listing-day open and close and the latest close, from Yahoo bars (Open/Close columns)."""
    import pandas as pd

    bars = bars.dropna(subset=["Close"])
    bars = bars[bars.index >= pd.Timestamp(listing_date)]
    if bars.empty or (bars.index[0].date() - listing_date).days > 3:
        return None
    return {
        "open": float(bars["Open"].iloc[0]),
        "close": float(bars["Close"].iloc[0]),
        "last": float(bars["Close"].iloc[-1]),
        "last_date": bars.index[-1].date().isoformat(),
    }


def listing_prices(service: IpoService, ipos: list[Ipo], today: date) -> dict[str, dict[str, float]]:
    """Prices since listing for recently listed issues; cached for the day."""
    listed = [i for i in ipos if i.market == "IN" and i.listing_date and i.listing_date <= today]
    if not listed:
        return {}
    name = f"listed-{today.isoformat()}.json"
    cached = service._read(name, OPEN_MAX_AGE_S) or {}
    missing = [i for i in listed if i.symbol not in cached]
    if missing:
        try:
            import yfinance as yf

            tickers = {f"{i.symbol}.NS": i for i in missing}
            raw = yf.download(
                list(tickers), start=min(i.listing_date for i in missing).isoformat(),
                auto_adjust=False, group_by="ticker", progress=False, threads=True,
            )  # fmt: skip
            for ticker, ipo in tickers.items():
                if raw is not None and ticker in raw.columns.get_level_values(0):
                    got = first_and_last(raw[ticker], ipo.listing_date)
                    if got:
                        cached[ipo.symbol] = got
            service._write(name, cached)
        except Exception as e:  # Yahoo hiccup: show the page without listing prices
            log.warning("IPO listing prices unavailable: %s", e)
    return cached
