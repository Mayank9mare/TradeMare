from datetime import date
from types import SimpleNamespace

import pandas as pd

from trademare.core.models import Market
from trademare.trading.outcomes import describe


class Store:
    def __init__(self, bars):
        self.bars = bars

    def read_bars(self, market, symbol):
        return self.bars.get(symbol)


def order(**kw):
    base = dict(
        market="IN", symbol="CHENNPETRO", side="BUY", quantity=4, order_type="LIMIT", limit_price=1406.6,
        status="OPEN", valid_until=date(2026, 10, 19), average_fill_price=None, filled_on=None, reason=None,
    )  # fmt: skip
    return SimpleNamespace(**(base | kw))


def test_waiting_and_filled():
    assert describe(order()) == (
        "waiting",
        "Buy 4 CHENNPETRO at ₹1,406.60 or less at the next open (valid until Mon 19 Oct).",
    )
    st, text = describe(order(status="FILLED", average_fill_price=3160.0, filled_on=date(2026, 10, 19)))
    assert st == "filled" and "filled at ₹3,160.00 on Mon 19 Oct" in text
    st, text = describe(order(side="SELL", order_type="MARKET", limit_price=None, market="US", symbol="V"))
    assert text == "Sell 4 V at the next open, at market."


def test_expired_says_why_from_that_sessions_prices():
    bars = pd.DataFrame(
        {"open": [1415.1], "high": [1430.0], "low": [1411.0], "close": [1420.0]},
        index=pd.DatetimeIndex([pd.Timestamp("2026-10-19")]),
    )
    st, text = describe(order(status="EXPIRED"), Store({"CHENNPETRO": bars}))
    assert st == "not filled"
    assert text == (
        "Buy 4 CHENNPETRO expired on Mon 19 Oct without filling: the lowest price, ₹1,411.00, "
        "stayed above your limit ₹1,406.60."
    )
    _, text = describe(order(status="EXPIRED"), Store({}))
    assert text.endswith("without filling: no prices for that session.")
    assert Market(order().market) is Market.IN


def test_price_freshness_counts_only_sessions_past_their_run_time():
    from datetime import UTC, datetime

    from tests.conftest import REPO_CONFIG
    from trademare.config import load_config
    from trademare.web.overview_data import price_freshness

    cfg = load_config(REPO_CONFIG).markets[Market.IN]  # benchmark ^NSEI, daily run 17:00 IST
    bars = pd.DataFrame({"close": [1.0]}, index=pd.DatetimeIndex([pd.Timestamp("2026-10-16")]))
    store = Store({"^NSEI": bars})
    # Mon 19 Oct, 16:00 IST: the day's run has not happened yet, so nothing is missing
    assert price_freshness(Market.IN, cfg, store, datetime(2026, 10, 19, 10, 30, tzinfo=UTC)) == (
        date(2026, 10, 16), 0,
    )  # fmt: skip
    # Tue 20 Oct, 00:30 IST: Monday's session finished and its prices are still missing
    assert price_freshness(Market.IN, cfg, store, datetime(2026, 10, 19, 19, 0, tzinfo=UTC))[1] == 1
