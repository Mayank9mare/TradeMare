"""Rule-based red flags from official corporate filings (no LLM, fully explainable).

Two levels:
- red: events that historically precede large losses (insolvency, payment default, fraud,
  auditor resignation, unreliable financials, trading suspension, pledge invocation);
- amber: events worth reading before trading (rating downgrade, key resignations, regulator
  orders and penalties, auditor change, pledge creation, results clarification sought).

Rules use the exchange's own category, refined by the announcement text. Routine monthly
"no default" disclosures are not flagged.

For companies that file only with BSE (whose data cannot be fetched automatically), news headlines
stand in for filings. Headlines are unverified and often about other firms, so a match there is
only ever a caution, titled "In the news: ...".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, timedelta

from trademare.data.events import SEC_ITEMS, Announcement, CorporateEvents

RED_LOOKBACK_DAYS = 180
AMBER_LOOKBACK_DAYS = 90


@dataclass(frozen=True)
class Flag:
    level: str  # red | amber
    title: str
    day: date
    detail: str
    source: str
    url: str | None = None
    count: int = 1  # same flag repeated in the window (the newest is shown)


def _rx(*words: str) -> re.Pattern[str]:
    return re.compile("|".join(words), re.IGNORECASE)


NIL = _rx(r"\bnil\b", r"no default", r"not applicable", r"no delay")
AUDITOR = _rx(r"auditor")
RESIGN = _rx(r"resign", r"cessation", r"step(?:ped|s)? down")
KEY_PEOPLE = _rx(
    r"\bceo\b", r"\bcfo\b", r"chief executive", r"chief financial", r"managing director", r"\bmd\b",
    r"independent director", r"whole[- ]time director", r"company secretary", r"chairman",
)  # fmt: skip
DOWNGRADE = _rx(r"downgrad", r"revised .*negative", r"outlook .*negative", r"credit watch .*negative")
INVOKE = _rx(r"invocation", r"invoked")
PLEDGE_BENIGN = _rx(r"invocation", r"invoked", r"release", r"revocation", r"creation of encumbrance.*nil")
PLEDGE = _rx(r"pledge")
QUALIFIED = _rx(
    r"qualified opinion", r"modified opinion", r"disclaimer of opinion", r"adverse opinion", r"going concern"
)
ORDERS = _rx(
    r"penalt", r"show cause", r"\bsebi\b.*order", r"enforcement directorate", r"\bsearch\b", r"\bdemand\b"
)


@dataclass(frozen=True)
class Rule:
    level: str
    title: str
    category: re.Pattern[str]
    text: re.Pattern[str] | None = None
    unless: re.Pattern[str] | None = None


DEBT_ONLY = _rx(
    r"non[- ]?convertible debenture", r"\bncds?\b", r"\bdebentures?\b", r"\bbonds?\b",
    r"commercial paper", r"on account of (?:maturity|redemption)",
)  # fmt: skip

NSE_RULES: list[Rule] = [
    Rule("red", "Insolvency proceedings", _rx(r"insolvency", r"\bcirp\b", r"liquidation")),
    Rule("red", "Fraud, default or arrest", _rx(r"fraud/default/arrest")),
    Rule("red", "Default on interest or principal", _rx(r"defaults? on payment"), unless=NIL),
    # Debentures and bonds stop trading when they mature or are redeemed: routine, not a red flag
    Rule("red", "Trading suspended", _rx(r"suspension of trading"), unless=DEBT_ONLY),
    Rule(
        "red",
        "Auditor resigned",
        _rx(r"change in auditors?", r"resignation"),
        _rx(r"auditor.*resign|resign.*auditor"),
    ),
    Rule("red", "Auditor's qualified opinion or going-concern doubt", _rx(r".*"), QUALIFIED),
    Rule("red", "Pledged shares invoked", _rx(r".*"), _rx(r"invocation of pledge|pledge.*invo|invo.*pledge")),
    Rule("amber", "Credit rating downgrade", _rx(r"credit rating"), DOWNGRADE),
    Rule("amber", "Change of auditor", _rx(r"change in auditors?")),
    Rule(
        "amber",
        "Key person resigned",
        _rx(r"resignation", r"cessation", r"change in (?:director|management)"),
        KEY_PEOPLE,
    ),
    Rule("amber", "Regulator action or order", _rx(r"action\(s\) (?:taken|initiated)", r"orders? passed")),
    Rule("amber", "Delay or default on dues to authorities", _rx(r"delay/default"), unless=NIL),
    Rule("amber", "Material litigation", _rx(r"pendency of litigation")),
    Rule("amber", "Shares pledged", _rx(r".*"), PLEDGE, unless=PLEDGE_BENIGN),
    Rule("amber", "Exchange sought clarification on results", _rx(r"clarification.*financial results")),
    Rule("amber", "Deviation in use of raised funds", _rx(r"statement of deviation"), unless=NIL),
]

NEWS_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("insolvency or bankruptcy", _rx(r"insolvency", r"\bnclt\b", r"bankrupt", r"liquidation")),
    ("default on payments", _rx(r"\bdefaults?\b(?! swap)", r"missed (?:interest|payment)")),
    ("fraud or raid", _rx(r"fraud", r"\braids?\b", r"\bsearch(?:es)?\b.*(?:income tax|\bed\b)", r"arrest")),
    ("auditor resigned", _rx(r"auditor.*(?:resign|quit)", r"(?:resign|quit).*auditor")),
    ("credit rating downgrade", _rx(r"downgrad.*rating", r"rating.*downgrad")),
    (
        "regulator order or penalty",
        _rx(r"\bsebi\b.*(?:order|penalt|bars?|ban)", r"penalt(?:y|ies) (?:of|on)"),
    ),
    ("pledged shares invoked", _rx(r"pledge.*invo", r"invo.*pledge")),
    ("trading suspended", _rx(r"suspen(?:d|sion).*trading", r"trading.*suspen")),
    ("CEO or CFO quit", _rx(r"\b(?:ceo|cfo|md)\b.*(?:resign|quit|steps? down)")),
]

SEC_RED = {"1.03", "2.04", "3.01", "4.02"}
SEC_AMBER = {"2.06", "4.01", "5.02"}


def _nse_flag(a: Announcement) -> Flag | None:
    for rule in NSE_RULES:
        if not rule.category.search(a.category):
            continue
        blob = f"{a.category} {a.text}"
        if rule.text is not None and not rule.text.search(blob):
            continue
        if rule.unless is not None and rule.unless.search(blob):
            continue
        return Flag(rule.level, rule.title, a.day, a.text or a.category, "NSE", a.url)
    return None


def _sec_flag(a: Announcement) -> Flag | None:
    if a.category.startswith("NT "):
        return Flag("red", "Late periodic filing", a.day, a.text, "SEC", a.url)
    for level, codes in (("red", SEC_RED), ("amber", SEC_AMBER)):
        hits = [c for c in a.items if c in codes]
        if hits:
            title = SEC_ITEMS[hits[0]] + (f" (+{len(hits) - 1} more)" if len(hits) > 1 else "")
            return Flag(level, title, a.day, f"{a.category} items {', '.join(a.items)}", "SEC", a.url)
    return None


def _news_flag(a: Announcement) -> Flag | None:
    for title, rx in NEWS_RULES:
        if rx.search(a.text):
            if title == "trading suspended" and DEBT_ONLY.search(a.text):
                continue  # a bond or debenture maturing, not the shares
            return Flag("amber", f"In the news: {title}", a.day, a.text, "News", a.url)
    return None


def red_flags(events: CorporateEvents | None, asof: date) -> list[Flag]:
    """Flags from announcements on or before ``asof``, newest first: red within 180 days, amber within 90."""
    if events is None:
        return []
    out = []
    for a in events.announcements:
        if a.day > asof:
            continue
        flag = {"NSE": _nse_flag, "News": _news_flag}.get(a.source, _sec_flag)(a)
        if flag is None:
            continue
        limit = RED_LOOKBACK_DAYS if flag.level == "red" else AMBER_LOOKBACK_DAYS
        if a.day >= asof - timedelta(days=limit):
            out.append(flag)
    grouped: dict[tuple[str, str], Flag] = {}
    for f in sorted(out, key=lambda f: f.day, reverse=True):
        key = (f.level, f.title)
        grouped[key] = replace(grouped[key], count=grouped[key].count + 1) if key in grouped else f
    return sorted(grouped.values(), key=lambda f: (f.level != "red", -f.day.toordinal()))


def coverage_note(events: CorporateEvents | None, asof: date) -> str | None:
    """Why the red-flag check may be blind for this symbol, if it is."""
    if events is None:
        return "corporate filings not checked"
    if events.error and not events.announcements:
        return f"could not fetch filings ({events.error})"
    if events.market == "IN" and not any(a.source == "NSE" for a in events.announcements):
        # listed companies file with NSE every few weeks, so none means a BSE-only filer
        if events.announcements:
            return (
                "no NSE filings (it files with BSE only, which can't be read automatically): "
                "news headlines were checked instead, which is less reliable"
            )
        return "no NSE filings in the last 6 months (it may file with BSE only): red-flag check unavailable"
    return None
