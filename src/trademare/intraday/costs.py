"""Zerodha intraday (MIS) equity charges, 2026 rates. Rates change; treat as close approximations.

Brokerage: the lower of ₹20 or 0.03% per executed order. STT 0.025% on the sell side only.
Exchange (NSE) 0.00297% and SEBI ₹10 per crore on both sides; stamp duty 0.003% on buys;
GST 18% on brokerage, exchange and SEBI charges.
"""

from __future__ import annotations

BROKERAGE_PCT = 0.0003
BROKERAGE_CAP = 20.0
STT_SELL = 0.00025
EXCHANGE = 0.0000297
SEBI = 0.000001
STAMP_BUY = 0.00003
GST = 0.18


def order_cost(value: float, buy: bool) -> float:
    """Charges on one executed order of ``value`` rupees."""
    brokerage = min(BROKERAGE_CAP, value * BROKERAGE_PCT)
    fees = value * (EXCHANGE + SEBI)
    total = brokerage + fees + GST * (brokerage + fees)
    total += value * STAMP_BUY if buy else value * STT_SELL
    return total


def round_trip(entry: float, exit_: float, qty: int, long: bool) -> float:
    """Charges for opening and closing one position (a short sells first)."""
    buy_value = (entry if long else exit_) * qty
    sell_value = (exit_ if long else entry) * qty
    return order_cost(buy_value, True) + order_cost(sell_value, False)


def round_trip_pct(entry: float, exit_: float, long: bool, notional: float) -> float:
    """Charges as a fraction of the entry value for a position of about ``notional`` rupees."""
    qty = max(1, int(notional // entry)) if entry > 0 else 1
    return round_trip(entry, exit_, qty, long) / (entry * qty)
