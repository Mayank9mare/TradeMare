"""Tax timing hint for exits: a profitable holding that turns long-term soon.

Both markets treat a share sold after more than a year as a long-term capital gain:
- India: held more than 12 months; listed equity is taxed 12.5% long-term vs 20% short-term
  (Finance Act 2024), and the first ₹1.25 lakh of long-term gains each year is exempt.
- US: held more than one year; long-term federal rates (0/15/20%) are below ordinary income rates.

The hint gives the date, the days left and, when ``tax`` rates are configured, a rough saving at
the current price. It is information, not advice: the price can fall more than the tax saved.
Shorts and losses get no hint.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from trademare.config.schema import TaxConfig


def long_term_from(entry: date) -> date:
    """First sale date that counts as long-term: more than one year after the purchase."""
    try:
        anniversary = entry.replace(year=entry.year + 1)
    except ValueError:  # 29 February
        anniversary = date(entry.year + 1, 3, 1)
    return anniversary + timedelta(days=1)


@dataclass(frozen=True)
class TaxHint:
    long_term_on: date
    days_left: int
    saving: float | None
    text: str


def tax_hint(
    cfg: TaxConfig, entry: date, today: date, gain: float, long: bool = True, currency: str = ""
) -> TaxHint | None:
    if not long or gain <= 0 or not cfg.warn_within_days:
        return None
    on = long_term_from(entry)
    days = (on - today).days
    if days <= 0 or days > cfg.warn_within_days:
        return None
    saving = None
    if cfg.short_term_rate_pct is not None and cfg.long_term_rate_pct is not None:
        saving = gain * (cfg.short_term_rate_pct - cfg.long_term_rate_pct) / 100
    text = f"Tax: selling now is a short-term gain; it turns long-term on {on} ({days} days)"
    if saving is not None:
        text += (
            f". At {cfg.short_term_rate_pct:g}% vs {cfg.long_term_rate_pct:g}% that saves about "
            f"{currency}{saving:,.0f} if the price holds"
        )
    return TaxHint(on, days, saving, text)
