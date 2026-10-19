# ruff: noqa: E501, RUF001  (one phrase per line reads best; times sign in user-facing text)
"""Signal reasons for people: sorted into for / against, in plain English.

The analysts write terse reasons ("Uptrend: price > SMA50 > SMA200", "Earnings yield 2% (#73 of 82
Industrials peers)"). A rank near the bottom of its peer group is a point *against* the trade even
though it is listed among the reasons, so each reason is judged on its own: peer ranks by where the
stock sits (top third for, bottom third against), technical phrases by what they say.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_RANK = re.compile(r"#(\d+) of (\d+)")
SKIP_KEYS = ("events", "tax")

# (pattern, plain-English rewrite, which way it points for a long trade: +1 for, -1 against, 0 neither)
_PHRASES: list[tuple[re.Pattern[str], str, int]] = [
    (re.compile(r"^Uptrend: price > SMA50 > SMA200$"), "In an uptrend: price above its 50- and 200-day averages", 1),
    (re.compile(r"^Downtrend: price < SMA50 < SMA200$"), "In a downtrend: price below its 50- and 200-day averages", -1),
    (re.compile(r"^Mixed trend.*$"), "No clear trend: price and its averages disagree", -1),
    (re.compile(r"^Pullback to SMA20 in uptrend \(RSI (\d+)\)$"), r"Dipped back to its 20-day average within an uptrend (RSI \1)", 1),
    (re.compile(r"^Bounce to SMA20 in downtrend \(RSI (\d+)\)$"), r"Bounced up to its 20-day average within a downtrend (RSI \1)", -1),
    (re.compile(r"^Extended: ([+-]\d+%) from SMA50, RSI (\d+) — poor long entry$"), r"Stretched: \1 from its 50-day average (RSI \2), a poor moment to buy", -1),
    (re.compile(r"^Oversold: ([+-]\d+%) from SMA50, RSI (\d+) — poor short entry$"), r"Oversold: \1 from its 50-day average (RSI \2), a poor moment to sell short", 1),
    (re.compile(r"^Breakout above (\d+)-day high on ([\d.]+)x volume$"), r"Broke above its \1-day high on \2× normal volume", 1),
    (re.compile(r"^Breakdown below (\d+)-day low on ([\d.]+)x volume$"), r"Fell below its \1-day low on \2× normal volume", -1),
    (re.compile(r"^Strong trend \(ADX (\d+)\)$"), r"Strong, steady trend (ADX \1)", 0),
    (re.compile(r"^(Index (?:above|below) its 200-day average \([^)]*\)), SMA50 above SMA200$"), r"\1; its 50-day average is above the 200-day (an uptrend)", 0),
    (re.compile(r"^(Index (?:above|below) its 200-day average \([^)]*\)), SMA50 below SMA200$"), r"\1; its 50-day average is below the 200-day (a downtrend)", 0),
    (re.compile(r"^Accumulation: up/down volume ratio ([\d.]+)$"), r"More volume on up days than down days (ratio \1)", 1),
    (re.compile(r"^Distribution: up/down volume ratio ([\d.]+)$"), r"More volume on down days than up days (ratio \1)", -1),
]  # fmt: skip


@dataclass
class Reasons:
    pro: list[str] = field(default_factory=list)
    con: list[str] = field(default_factory=list)
    neutral: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if not (self.pro or self.con):
            return ""
        lead = self.pro[0] if self.pro else "Nothing clearly in its favour"
        n = len(self.con)
        tail = f"; {n} point{'s' if n != 1 else ''} against" if n else "; nothing noted against"
        return lead + tail + "."


def plain(text: str) -> tuple[str, int]:
    """(plain-English text, direction for a long: +1 / -1 / 0)."""
    for pattern, rewrite, sign in _PHRASES:
        if pattern.match(text):
            return pattern.sub(rewrite, text), sign
    if m := _RANK.search(text):
        place, size = int(m.group(1)), int(m.group(2))
        if size >= 3:
            share = place / size
            return text, 1 if share <= 1 / 3 else -1 if share > 2 / 3 else 0
    if m := re.match(r"^6-month return ([+-]\d+)%", text):
        return text, 1 if int(m.group(1)) > 0 else -1
    return text, 0


def split_reasons(reasons: dict | None, direction: str = "LONG", limit: int = 6) -> Reasons:
    out = Reasons()
    flip = -1 if direction == "SHORT" else 1
    seen: set[str] = set()
    for key, items in (reasons or {}).items():
        if key in SKIP_KEYS:
            continue
        for raw in items or []:
            text, sign = plain(str(raw))
            if text in seen:
                continue
            seen.add(text)
            bucket = out.pro if sign * flip > 0 else out.con if sign * flip < 0 else out.neutral
            if len(bucket) < limit:
                bucket.append(text)
    return out
