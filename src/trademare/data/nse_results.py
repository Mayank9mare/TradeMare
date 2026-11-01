"""Indian quarterly results straight from NSE filings: real publication times and the numbers.

Yahoo, the main fundamentals source, has no filing dates (TradeMare assumes the SEBI deadline,
45 days after the quarter) and often adds a quarter days or weeks after the company publishes.
NSE's public filing lists give, for every results filing, the quarter, whether it is consolidated
and the exact broadcast time, plus an XBRL file with the figures:

- ``integrated-filing-results`` (the format used since 2025, with XBRL in the ``in-capmkt``
  taxonomy), and
- ``corporates-financial-results`` (older filings: dates only are used here).

``overlay`` merges this into a Yahoo financials frame: quarters (and the fiscal year ending with
a March quarter) become available from their real broadcast time, and quarters Yahoo does not
have yet are added with the figures the XBRL file carries. A filing broadcast at or after 17:00
IST counts from the next day, after TradeMare's daily run. Requests are spaced out and cached; the
caller decides which symbols to refresh (see ``recent_filers``).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from trademare.data.fields import FIELDS, normalize_financials

log = logging.getLogger(__name__)

INTEGRATED = (
    "https://www.nseindia.com/api/integrated-filing-results?index=equities&symbol={symbol}"
    "&type=Integrated%20Filing-%20Financials"
)
INTEGRATED_ALL = (
    "https://www.nseindia.com/api/integrated-filing-results?index=equities"
    "&type=Integrated%20Filing-%20Financials&page={page}"
)
OLDER = "https://www.nseindia.com/api/corporates-financial-results?index=equities&symbol={symbol}&period=Quarterly"
CUTOFF = time(17, 0)  # TradeMare's daily run; later filings count from the next day
XBRL_QUARTERS = 5  # figures for the latest quarters (5 = includes the year-ago quarter)


@dataclass
class Filing:
    quarter_end: str  # ISO date
    broadcast: str | None  # ISO datetime (IST)
    consolidated: bool
    original: bool
    xbrl: str | None
    fmt: str  # "integrated" | "older"


@dataclass
class SymbolResults:
    symbol: str
    fetched_at: str
    filings: list[Filing] = field(default_factory=list)
    figures: dict[str, dict[str, float]] = field(default_factory=dict)  # quarter_end -> field -> value
    error: str | None = None


def _dt(text: str | None) -> datetime | None:
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y"):
        try:
            return datetime.strptime((text or "").strip(), fmt)
        except ValueError:
            continue
    return None


def _day(text: str | None) -> str | None:
    d = _dt(text)
    return d.date().isoformat() if d else None


def available_day(broadcast: str | None) -> date | None:
    """First session date the filing can be used: the broadcast day, or the next if after 17:00."""
    if not broadcast:
        return None
    ts = datetime.fromisoformat(broadcast)
    return ts.date() + timedelta(days=1) if ts.time() >= CUTOFF else ts.date()


# ---------------------------------------------------------------- XBRL figures
_TAGS = {
    "revenue": ("RevenueFromOperations",),
    # Total profit first: some filers leave the "attributable to owners" line at 0.
    "net_income": ("ProfitLossForPeriod", "ProfitOrLossAttributableToOwnersOfParent"),
    "interest_expense": ("FinanceCosts",),
    "depreciation": ("DepreciationDepletionAndAmortisationExpense",),
}
_INSTANT = {
    "total_equity": ("EquityAttributableToOwnersOfParent", "Equity"),
}


def parse_xbrl(text: str) -> tuple[str | None, dict[str, float]]:
    """(quarter end, figures) from an integrated-filing XBRL file (``in-capmkt`` taxonomy).

    Uses only contexts without dimensions: the quarter's own duration (about three months ending
    on the reporting date) for flows, and the reporting date for balance-sheet values."""
    end_m = re.search(r"<in-capmkt:DateOfEndOfReportingPeriod[^>]*>([^<]+)<", text)
    if not end_m:
        return None, {}
    end = end_m.group(1).strip()
    durations, instants = set(), set()
    for cid, body in re.findall(r'<xbrli:context id="([^"]+)">(.*?)</xbrli:context>', text, re.S):
        if "xbrldi:" in body:
            continue
        s = re.search(r"<xbrli:startDate>([^<]+)<", body)
        e = re.search(r"<xbrli:endDate>([^<]+)<", body)
        i = re.search(r"<xbrli:instant>([^<]+)<", body)
        if s and e and e.group(1) == end:
            days = (date.fromisoformat(e.group(1)) - date.fromisoformat(s.group(1))).days
            if 80 <= days <= 100:
                durations.add(cid)
        elif i and i.group(1) == end:
            instants.add(cid)

    def value(tag: str, contexts: set[str]) -> float | None:
        for cid, raw in re.findall(rf'<in-capmkt:{tag}\b[^>]*contextRef="([^"]+)"[^>]*>([^<]*)<', text):
            if cid in contexts:
                try:
                    return float(raw)
                except ValueError:
                    return None
        return None

    out: dict[str, float] = {}
    for name, tags in _TAGS.items():
        for tag in tags:
            if (v := value(tag, durations)) is not None:
                out[name] = v
                break
    for name, tags in _INSTANT.items():
        for tag in tags:
            if (v := value(tag, instants)) is not None:
                out[name] = v
                break
    paid = value("PaidUpValueOfEquityShareCapital", durations | instants)
    face = value("FaceValueOfEquityShareCapital", durations | instants)
    if paid and face:
        out["shares_outstanding"] = paid / face
    return end, out


# ---------------------------------------------------------------- service
class NseResults:
    def __init__(self, cache_dir: str | Path, nse=None, max_age_hours: float = 20) -> None:
        self.root = Path(cache_dir) / "nse_results"
        self._nse = nse
        self.max_age = timedelta(hours=max_age_hours)

    @property
    def nse(self):
        if self._nse is None:
            from trademare.data.events import NseClient

            self._nse = NseClient(pause=1.5)
        return self._nse

    def _path(self, symbol: str) -> Path:
        return self.root / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', symbol)}.json"

    def cached(self, symbol: str) -> SymbolResults | None:
        p = self._path(symbol)
        if not p.exists():
            return None
        try:
            d = json.loads(p.read_text())
            d["filings"] = [Filing(**f) for f in d.get("filings", [])]
            return SymbolResults(**d)
        except (ValueError, TypeError, KeyError):
            return None

    def get(
        self, symbol: str, refresh: bool = False, figures: bool = True, quarters: int = XBRL_QUARTERS
    ) -> SymbolResults:
        """Filings (and figures for the latest quarters) for one symbol, cached for about a day."""
        hit = self.cached(symbol)
        if hit and not refresh and not hit.error:
            age = datetime.now() - datetime.fromisoformat(hit.fetched_at)
            if age < self.max_age:
                return hit
        res = SymbolResults(symbol, datetime.now().isoformat(timespec="seconds"))
        try:
            res.filings = self._filings(symbol)
        except Exception as e:  # NSE blocking or format changes: report, never raise
            res.error = f"{type(e).__name__}: {e}"
            if hit:
                hit.error = f"stale: {res.error}"
                return hit
        res.figures = dict(hit.figures) if hit else {}
        if figures and not res.error:
            self._fetch_figures(res, quarters)
        self._path(symbol).parent.mkdir(parents=True, exist_ok=True)
        self._path(symbol).write_text(json.dumps(asdict(res), indent=1))
        return res

    def _filings(self, symbol: str) -> list[Filing]:
        from requests.utils import quote

        out: list[Filing] = []
        data = self.nse.get_json(INTEGRATED.format(symbol=quote(symbol)))
        for x in data.get("data", []) if isinstance(data, dict) else []:
            q = _day(x.get("qe_Date"))
            if not q:
                continue
            b = _dt(x.get("broadcast_Date"))
            consolidated = (x.get("consolidated") or "").startswith("Cons")
            original = (x.get("type_Sub") or "Original") == "Original"
            out.append(
                Filing(q, b.isoformat() if b else None, consolidated, original, x.get("xbrl"), "integrated")
            )
        older = self.nse.get_json(OLDER.format(symbol=quote(symbol)))
        for x in older if isinstance(older, list) else []:
            q = _day(x.get("toDate"))
            b = _dt(x.get("broadCastDate") or x.get("filingDate"))
            if q:
                consolidated = (x.get("consolidated") or "").startswith("Cons")
                out.append(
                    Filing(q, b.isoformat() if b else None, consolidated, True, x.get("xbrl"), "older")
                )
        return out

    def _fetch_figures(self, res: SymbolResults, quarters: int = XBRL_QUARTERS) -> None:
        recent = sorted({f.quarter_end for f in res.filings if f.fmt == "integrated"}, reverse=True)
        for q in recent[:quarters]:
            if q in res.figures:
                continue
            cands = [
                f
                for f in res.filings
                if f.quarter_end == q and f.fmt == "integrated" and f.xbrl and f.original
            ]
            cands.sort(key=lambda f: not f.consolidated)  # consolidated first, as Yahoo reports
            for f in cands:
                try:
                    r = self.nse.session.get(f.xbrl, timeout=30)
                    r.raise_for_status()
                    end, figs = parse_xbrl(r.text)
                except Exception as e:
                    log.warning("%s %s XBRL: %s", res.symbol, q, e)
                    continue
                if end == q and figs:
                    res.figures[q] = figs
                    break

    def recent_filers(self, since: datetime, max_pages: int = 25) -> set[str]:
        """Symbols with a results filing broadcast at or after ``since`` (market-wide feed)."""
        found: set[str] = set()
        for page in range(max_pages):
            data = self.nse.get_json(INTEGRATED_ALL.format(page=page))
            rows = data.get("data", []) if isinstance(data, dict) else []
            if not rows:
                break
            older = 0
            for x in rows:
                b = _dt(x.get("broadcast_Date"))
                if b is None or b >= since:
                    found.add(x.get("symbol"))
                else:
                    older += 1
            if older == len(rows):
                break
        return {s for s in found if s}


# ---------------------------------------------------------------- merge into a financials frame
def overlay(financials: pd.DataFrame, res: SymbolResults | None) -> pd.DataFrame:
    """Real publication dates and missing latest quarters from NSE, on top of a Yahoo frame."""
    if res is None or not res.filings:
        return financials
    first: dict[str, date] = {}
    for f in res.filings:
        day = available_day(f.broadcast)
        if day is not None and (f.quarter_end not in first or day < first[f.quarter_end]):
            first[f.quarter_end] = day
    df = financials.copy()
    if not df.empty:
        ends = df["period_end"].dt.date.astype(str)
        for i in df.index:
            q = ends[i]
            if q in first and (df.at[i, "period_type"] == "Q" or q.endswith("-03-31")):
                df.at[i, "available_from"] = pd.Timestamp(first[q])
    have = set(df.loc[df["period_type"] == "Q", "period_end"].dt.date.astype(str)) if not df.empty else set()
    extra = []
    for q, figs in res.figures.items():
        if q in have or q not in first:
            continue
        row: dict[str, Any] = {
            "period_type": "Q",
            "period_end": pd.Timestamp(q),
            "available_from": pd.Timestamp(first[q]),
        }
        row.update({k: v for k, v in figs.items() if k in FIELDS})
        extra.append(row)
    if extra:
        df = pd.concat([df, pd.DataFrame(extra)], ignore_index=True)
    return normalize_financials(df)


def latest_result(res: SymbolResults | None) -> dict[str, Any] | None:
    """The most recent quarter's publication date and headline figures, for the brief."""
    if res is None or not res.filings:
        return None
    q = max(f.quarter_end for f in res.filings)
    days = [available_day(f.broadcast) for f in res.filings if f.quarter_end == q and f.broadcast]
    figs = res.figures.get(q, {})
    end = date.fromisoformat(q)
    prior = res.figures.get(end.replace(year=end.year - 1).isoformat(), {})

    def growth(key: str) -> float | None:
        a, b = figs.get(key), prior.get(key)
        return round(a / b - 1, 4) if a is not None and b and b > 0 else None

    return {
        "quarter_end": q,
        "published": min(days).isoformat() if days else None,
        "revenue": figs.get("revenue"),
        "net_income": figs.get("net_income"),
        "revenue_growth_yoy": growth("revenue"),
        "profit_growth_yoy": growth("net_income"),
    }
