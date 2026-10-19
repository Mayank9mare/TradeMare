# ruff: noqa: RUF001  (times and dash signs in user-facing text)
"""What the IPO page and the IPO alerts show for each issue.

Each Indian mainboard issue gets its terms, the subscription so far, the track record of past
issues with similar QIB demand (from the pre-registered study) and, only if that study passed, its
fixed label. Nothing here applies for an IPO: that happens in the owner's bank or broker app.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from trademare.analysis.ipo_study import BucketStats, StudyResult, bucket_of
from trademare.data.ipo import Ipo

KIND_TEXT = {
    "fresh": "Fresh issue: the money goes to the company",
    "ofs": "Offer for sale only: existing shareholders are selling; the company raises nothing",
    "fresh+ofs": "Fresh issue plus offer for sale: part goes to the company, part to selling shareholders",
}


@dataclass
class IpoCard:
    ipo: Ipo
    status: str
    bucket: str | None = None
    stats: BucketStats | None = None
    label: str | None = None
    retail_odds: float | None = None
    listing: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def symbol(self) -> str:
        return self.ipo.symbol

    @property
    def qib(self) -> float | None:
        return self.ipo.subscription.qib if self.ipo.subscription else None

    @property
    def listing_gain(self) -> float | None:
        if self.listing and self.ipo.price:
            return self.listing["close"] / self.ipo.price - 1
        return None

    @property
    def gain_since(self) -> float | None:
        if self.listing and self.ipo.price:
            return self.listing["last"] / self.ipo.price - 1
        return None


def retail_odds(retail: float | None) -> float | None:
    """Rough chance of one lot for a retail applicant: about 1 in ``retail`` when most apply for one
    lot (better if many applied for more). None below 2x, where most applicants get a lot."""
    if retail is None or retail < 2:
        return None
    return 1 / retail


def build_card(ipo: Ipo, today: date, study: StudyResult | None, listing: dict | None = None) -> IpoCard:
    status = ipo.status(today)
    card = IpoCard(ipo=ipo, status=status, listing=listing)
    sub = ipo.subscription
    if sub:
        card.bucket = bucket_of(sub.qib)
        card.retail_odds = retail_odds(sub.retail)
        if study:
            card.stats = study.stats_for(sub.qib)
            card.label = study.label(sub.qib)
    facts = ipo.facts
    if facts and facts.kind == "ofs":
        card.notes.append("All shares are sold by existing owners; none of the money reaches the company.")
    if status == "open" and sub:
        card.notes.append(
            "Subscription is a running count. Institutions bid mostly on the last day, so the QIB figure "
            "usually rises until the 5 pm close; the track record uses final figures."
        )
    if status == "upcoming":
        card.notes.append("Bidding has not started; demand figures appear once the issue opens.")
    return card


def build_cards(
    ipos: list[Ipo], today: date, study: StudyResult | None, listings: dict[str, dict] | None = None
) -> list[IpoCard]:
    listings = listings or {}
    return [build_card(i, today, study, listings.get(i.symbol)) for i in ipos]


def ipo_facts(card: IpoCard) -> dict[str, Any]:
    """The evidence given to Claude: what the page shows, nothing more."""
    ipo, sub, st = card.ipo, card.ipo.subscription, card.stats
    return {
        "company": ipo.company,
        "symbol": ipo.symbol,
        "status": card.status,
        "bidding": f"{ipo.start} to {ipo.end}",
        "price_band_inr": [ipo.price_low, ipo.price_high],
        "final_price_inr": ipo.issue_price,
        "lot_shares": ipo.facts.lot if ipo.facts else None,
        "min_investment_inr": ipo.min_investment,
        "issue_value_inr": ipo.issue_value,
        "issue_type": KIND_TEXT.get(ipo.facts.kind, "") if ipo.facts else "",
        "issue_text": ipo.facts.issue_text if ipo.facts else "",
        "lead_managers": ipo.facts.lead_managers if ipo.facts else "",
        "subscription_times": (
            {
                "qib": sub.qib,
                "nii": sub.nii,
                "retail": sub.retail,
                "employee": sub.employee,
                "total": sub.total,
                "as_of": sub.as_of,
            }
            if sub
            else None
        ),
        "qib_bucket": card.bucket,
        "past_issues_in_bucket": (
            {
                "count": st.count,
                "median_listing_day_gain": st.median_gain,
                "share_listing_above_issue": st.share_above,
                "median_6_month_return_minus_nifty": st.median_excess_6m,
            }
            if st
            else None
        ),
        "label_from_fixed_rule": card.label,
        "listing_day_gain": card.listing_gain,
        "notes": card.notes,
    }


def _times(x: float | None) -> str:
    return "–" if x is None else f"{x:.1f}×"


def ipo_alerts(cards: list[IpoCard], today: date, now_hour: int) -> list[tuple[str, str]]:
    """(dedupe key, message) pairs: an issue opening soon, and the last afternoon of bidding."""
    out = []
    for c in cards:
        ipo = c.ipo
        if c.status == "upcoming" and ipo.start and (ipo.start - today).days <= 2:
            out.append(
                (
                    f"ipo-opens:{ipo.symbol}",
                    f"IPO opening {ipo.start:%a %d %b}: {ipo.company} ({ipo.symbol}), "
                    f"₹{ipo.price_low:,.0f}–{ipo.price_high:,.0f}"
                    + (f", lot {ipo.facts.lot} (₹{ipo.min_investment:,.0f})" if ipo.min_investment else "")
                    + f", bidding until {ipo.end:%a %d %b}.",
                )
            )
        if c.status == "open" and ipo.end == today and now_hour >= 12:
            sub = ipo.subscription
            line = f"IPO closes today 5 pm: {ipo.company} ({ipo.symbol})."
            if sub:
                line += (
                    f" Subscribed so far: QIB {_times(sub.qib)}, retail {_times(sub.retail)},"
                    f" total {_times(sub.total)}."
                )
            if c.stats and c.stats.count:
                line += (
                    f" Past issues with QIB {c.bucket}: {c.stats.count}, median listing-day gain "
                    f"{c.stats.median_gain:+.0%}, {c.stats.share_above:.0%} listed above issue."
                )
            if c.label:
                line += f" Rule: {c.label}."
            out.append((f"ipo-closes:{ipo.symbol}", line))
    return out
