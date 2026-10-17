"""Transaction cost models (statutory charges; brokerage assumed zero for delivery/API).

India (equity delivery, Zerodha/Dhan, rates as of 2025-26):
  STT 0.1% on buy and sell, exchange txn 0.00297% (NSE), SEBI fee 0.0001%,
  stamp duty 0.015% on buy, GST 18% on (brokerage + exchange + SEBI),
  DP charge ~₹15.93 (₹13.50 + GST) per scrip per sell day.
US (Alpaca, commission-free):
  SEC fee $27.80 per $1M sold, FINRA TAF $0.000166/share sold (max $8.30).

Rates change; treat these as close approximations and update when regulators do.
"""

from __future__ import annotations

from dataclasses import dataclass

from trademare.core.models import Market, OrderSide


@dataclass(frozen=True)
class IndiaDeliveryCosts:
    stt: float = 0.001
    exchange: float = 0.0000297
    sebi: float = 0.000001
    stamp_buy: float = 0.00015
    gst: float = 0.18
    dp_per_sell: float = 15.93  # already includes GST

    def cost(self, side: OrderSide, price: float, qty: int) -> float:
        value = price * qty
        exchange_and_sebi = value * (self.exchange + self.sebi)
        total = value * self.stt + exchange_and_sebi + exchange_and_sebi * self.gst
        if side is OrderSide.BUY:
            total += value * self.stamp_buy
        else:
            total += self.dp_per_sell
        return total


@dataclass(frozen=True)
class USEquityCosts:
    sec_fee: float = 27.80 / 1_000_000
    taf_per_share: float = 0.000166
    taf_max: float = 8.30

    def cost(self, side: OrderSide, price: float, qty: int) -> float:
        if side is OrderSide.BUY:
            return 0.0
        return price * qty * self.sec_fee + min(qty * self.taf_per_share, self.taf_max)


def cost_model(market: Market) -> IndiaDeliveryCosts | USEquityCosts:
    return IndiaDeliveryCosts() if market is Market.IN else USEquityCosts()
