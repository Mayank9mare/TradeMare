# ruff: noqa: RUF001  (times and dash signs in user-facing text)
"""The pre-registered IPO study (docs/research/2026-10-19-ipo-demand-plan.md) and the label it allows.

Does the final QIB subscription of an NSE mainboard IPO predict its listing-day gain? The study
pulls every listed mainboard issue since 2016, its final subscription from NSE and its prices from
Yahoo, buckets issues by QIB demand and tests the rank correlation in each half of the sample. The
result is saved as JSON; the IPO page reads it to show each bucket's track record and, only if
the test passed, a Favourable / Mixed / Unfavourable label.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("< 1×", 0.0, 1.0),
    ("1–10×", 1.0, 10.0),
    ("10–50×", 10.0, 50.0),
    ("50–100×", 50.0, 100.0),
    ("≥ 100×", 100.0, math.inf),
)
SPLIT = date(2022, 1, 1)  # first half: issues closing before this; second half: from it
START, END = date(2016, 1, 1), date(2026, 10, 17)
HOLD_DAYS = 126
LISTING_SLACK_DAYS = 3


def bucket_of(qib: float | None) -> str | None:
    if qib is None or qib < 0:
        return None
    for name, lo, hi in BUCKETS:
        if lo <= qib < hi:
            return name
    return None


@dataclass
class Outcome:
    symbol: str
    end: date
    qib: float
    issue_price: float
    listing_open: float
    listing_close: float
    gain_open: float
    gain_close: float
    six_month: float | None = None
    nifty_six_month: float | None = None


def listing_outcome(
    symbol: str,
    end: date,
    qib: float,
    issue_price: float,
    listing_date: date,
    bars: pd.DataFrame,
    nifty: pd.Series | None = None,
) -> Outcome | None:
    """Gains against the issue price, using unadjusted prices with later splits undone.

    ``bars`` has Open, Close and (optionally) Stock Splits columns indexed by date, as Yahoo returns
    with ``auto_adjust=False, actions=True``: prices are split-adjusted, so the issue price is
    divided by every split after listing to compare like with like.
    """
    bars = bars.dropna(subset=["Close"])
    bars = bars[bars.index >= pd.Timestamp(listing_date)]
    if bars.empty or (bars.index[0].date() - listing_date).days > LISTING_SLACK_DAYS:
        return None
    splits = bars["Stock Splits"] if "Stock Splits" in bars else pd.Series(dtype=float)
    factor = float(splits[(splits > 0) & (splits.index > bars.index[0])].prod()) if len(splits) else 1.0
    price = issue_price / (factor or 1.0)
    first = bars.iloc[0]
    out = Outcome(
        symbol, end, qib, issue_price, float(first["Open"]), float(first["Close"]),
        float(first["Open"]) / price - 1, float(first["Close"]) / price - 1,
    )  # fmt: skip
    if len(bars) > HOLD_DAYS:
        day = bars.index[HOLD_DAYS]
        out.six_month = float(bars["Close"].iloc[HOLD_DAYS]) / price - 1
        if nifty is not None:
            n = nifty.dropna()
            before, after = n[n.index <= bars.index[0]], n[n.index <= day]
            if len(before) and len(after):
                out.nifty_six_month = float(after.iloc[-1]) / float(before.iloc[-1]) - 1
    return out


def spearman(x: pd.Series, y: pd.Series) -> tuple[float, float, int]:
    """Rank correlation and a two-sided p-value (normal approximation, fine above ~30 points)."""
    df = pd.DataFrame({"x": x, "y": y}).dropna()
    n = len(df)
    if n < 4:
        return float("nan"), float("nan"), n
    r = float(df["x"].rank().corr(df["y"].rank()))
    if abs(r) >= 1:
        return r, 0.0, n
    t = r * math.sqrt((n - 2) / (1 - r * r))
    return r, math.erfc(abs(t) / math.sqrt(2)), n


@dataclass
class BucketStats:
    bucket: str
    count: int
    median_gain: float | None
    share_above: float | None
    median_open_gain: float | None
    median_excess_6m: float | None
    count_6m: int


def bucket_stats(outcomes: list[Outcome]) -> list[BucketStats]:
    df = pd.DataFrame([asdict(o) for o in outcomes])
    out = []
    for name, _lo, _hi in BUCKETS:
        g = df[df["qib"].map(bucket_of) == name] if len(df) else df
        excess = (g["six_month"] - g["nifty_six_month"]).dropna() if len(g) else pd.Series(dtype=float)
        out.append(
            BucketStats(
                bucket=name,
                count=len(g),
                median_gain=float(g["gain_close"].median()) if len(g) else None,
                share_above=float((g["gain_close"] > 0).mean()) if len(g) else None,
                median_open_gain=float(g["gain_open"].median()) if len(g) else None,
                median_excess_6m=float(excess.median()) if len(excess) else None,
                count_6m=len(excess),
            )
        )
    return out


def label_for(stats: BucketStats | None) -> str | None:
    """The plan's fixed rule; None when the bucket has no history."""
    if stats is None or not stats.count or stats.share_above is None or stats.median_gain is None:
        return None
    if stats.share_above >= 0.75 and stats.median_gain >= 0.10:
        return "Favourable"
    if stats.share_above < 0.60 or stats.median_gain < 0:
        return "Unfavourable"
    return "Mixed"


def summarize(outcomes: list[Outcome], dropped: dict[str, int]) -> dict[str, Any]:
    df = pd.DataFrame([asdict(o) for o in outcomes])
    halves = {}
    for name, part in (("2016–2021", df[df["end"] < SPLIT]), ("2022–2026", df[df["end"] >= SPLIT])):
        r, p, n = spearman(part["qib"], part["gain_close"])
        halves[name] = {"rho": r, "p": p, "n": n}
    r, p, n = spearman(df["qib"], df["gain_close"])
    h1 = all(h["n"] >= 4 and h["rho"] > 0 and h["p"] < 0.05 for h in halves.values())
    return {
        "generated": date.today().isoformat(),
        "sample": {"start": START.isoformat(), "end": END.isoformat(), "n": len(df), "dropped": dropped},
        "halves": halves,
        "full": {"rho": r, "p": p, "n": n},
        "h1_holds": h1,
        "buckets": [asdict(b) for b in bucket_stats(outcomes)],
    }


@dataclass
class StudyResult:
    h1_holds: bool
    buckets: dict[str, BucketStats]
    generated: str
    n: int

    def stats_for(self, qib: float | None) -> BucketStats | None:
        name = bucket_of(qib)
        return self.buckets.get(name) if name else None

    def label(self, qib: float | None) -> str | None:
        return label_for(self.stats_for(qib)) if self.h1_holds else None

    def bucket_label(self, bucket: str) -> str | None:
        return label_for(self.buckets.get(bucket)) if self.h1_holds else None


def study_path(cache_dir: str | Path) -> Path:
    return Path(cache_dir) / "ipo" / "study.json"


def load_study(cache_dir: str | Path) -> StudyResult | None:
    try:
        d = json.loads(study_path(cache_dir).read_text())
    except (OSError, ValueError):
        return None
    buckets = {b["bucket"]: BucketStats(**b) for b in d.get("buckets", [])}
    return StudyResult(bool(d.get("h1_holds")), buckets, d.get("generated", ""), d["sample"]["n"])


def run_study(service, cache_dir: str | Path, progress=print) -> dict[str, Any]:
    """Fetch everything the plan needs and save the summary. Slow the first time (one NSE request
    per issue, spaced out); subscription figures for closed issues are cached for good."""
    import yfinance as yf

    from trademare.core.models import Market
    from trademare.data.providers.yfinance_provider import yf_symbol

    issues = [
        i for i in service.past(max_age=0)
        if i.issue_price and i.listing_date and i.end and START <= i.listing_date <= END
    ]  # fmt: skip
    dropped = {"no_subscription": 0, "no_prices": 0}
    qib: dict[str, float] = {}
    for k, ipo in enumerate(issues, 1):
        sub = service.subscription(ipo.symbol, final=True)
        if sub and sub.qib is not None:
            qib[ipo.symbol] = sub.qib
        else:
            dropped["no_subscription"] += 1
        if k % 50 == 0:
            progress(f"subscriptions: {k}/{len(issues)}")
    issues = [i for i in issues if i.symbol in qib]
    tickers = [yf_symbol(Market.IN, i.symbol) for i in issues]
    progress(f"prices for {len(tickers)} issues from Yahoo…")
    raw = yf.download(
        [*tickers, "^NSEI"], start=(START - timedelta(days=10)).isoformat(),
        auto_adjust=False, actions=True, group_by="ticker", progress=False, threads=True,
    )  # fmt: skip
    nifty = raw["^NSEI"]["Close"] if "^NSEI" in raw.columns.get_level_values(0) else None
    outcomes = []
    for ipo, ticker in zip(issues, tickers, strict=True):
        bars = raw[ticker] if ticker in raw.columns.get_level_values(0) else pd.DataFrame()
        o = None
        if not bars.empty:
            o = listing_outcome(
                ipo.symbol, ipo.end, qib[ipo.symbol], ipo.issue_price, ipo.listing_date, bars, nifty
            )
        if o is None:
            dropped["no_prices"] += 1
        else:
            outcomes.append(o)
    summary = summarize(outcomes, dropped)
    summary["issues"] = [asdict(o) for o in outcomes]
    path = study_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, default=str, indent=1))
    return summary
