import pandas as pd
import pytest

from trademare.core.models import Market
from trademare.data.providers.edgar import EdgarFundamentalsProvider, SecClient, companyfacts_to_financials
from trademare.data.providers.yfinance_provider import statements_to_financials, yf_symbol
from trademare.data.universe import _parse_nifty, _parse_sp500


def fact(val, end, filed, start=None, form="10-Q"):
    d = {"end": end, "val": val, "filed": filed, "form": form}
    if start:
        d["start"] = start
    return d


def payload():
    fy_start = "2024-01-01"
    revenue = [
        # FY2024 3-month and YTD facts, as filed in 10-Qs and the 10-K
        fact(100, "2024-03-31", "2024-05-01", fy_start),
        fact(110, "2024-06-30", "2024-08-01", "2024-04-01"),
        fact(210, "2024-06-30", "2024-08-01", fy_start),
        fact(330, "2024-09-30", "2024-11-01", fy_start),
        fact(460, "2024-12-31", "2025-02-15", fy_start, form="10-K"),
        # restated FY2024 value in the next 10-K must not overwrite the original
        fact(999, "2024-12-31", "2026-02-15", fy_start, form="10-K"),
        # 8-K facts are ignored
        fact(5, "2024-03-31", "2024-04-15", fy_start, form="8-K"),
    ]
    ocf = [  # cash flow is YTD-only in 10-Qs
        fact(20, "2024-03-31", "2024-05-01", fy_start),
        fact(50, "2024-06-30", "2024-08-01", fy_start),
        fact(90, "2024-09-30", "2024-11-01", fy_start),
        fact(130, "2024-12-31", "2025-02-15", fy_start, form="10-K"),
    ]
    net_income = [
        fact(10, "2024-03-31", "2024-05-01", fy_start),
        fact(40, "2024-12-31", "2025-02-15", fy_start, "10-K"),
    ]
    assets = [fact(1000, "2024-03-31", "2024-05-01"), fact(1200, "2024-12-31", "2025-02-15", form="10-K")]
    usd = lambda xs: {"units": {"USD": xs}}  # noqa: E731
    return {
        "facts": {
            "us-gaap": {
                "Revenues": usd(revenue),
                "NetCashProvidedByUsedInOperatingActivities": usd(ocf),
                "NetIncomeLoss": usd(net_income),
                "Assets": usd(assets),
                "OperatingIncomeLoss": usd([fact(30, "2024-03-31", "2024-05-01", fy_start)]),
                "DepreciationDepletionAndAmortization": usd([fact(5, "2024-03-31", "2024-05-01", fy_start)]),
                "LongTermDebt": usd([fact(300, "2024-03-31", "2024-05-01")]),
                "CommercialPaper": usd([fact(50, "2024-03-31", "2024-05-01")]),
            },
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {"shares": [fact(7e6, "2024-04-20", "2024-05-01", form="10-Q")]}
                }
            },
        }
    }


@pytest.fixture
def edgar_df():
    return companyfacts_to_financials(payload()).set_index(["period_type", "period_end"])


def test_edgar_quarters_rebuilt_from_ytd(edgar_df):
    q = edgar_df.loc["Q"]
    assert q["revenue"].tolist() == [100, 110, 120, 130]  # Q3 = 330-210, Q4 = 460-330
    assert q["operating_cash_flow"].tolist() == [20, 30, 40, 40]


def test_edgar_point_in_time_dates(edgar_df):
    assert edgar_df.loc[("Q", pd.Timestamp("2024-09-30")), "available_from"] == pd.Timestamp("2024-11-01")
    annual = edgar_df.loc[("A", pd.Timestamp("2024-12-31"))]
    assert annual["revenue"] == 460  # original, not the 2026 restatement
    assert annual["available_from"] == pd.Timestamp("2025-02-15")
    assert annual["total_assets"] == 1200


def test_edgar_derived_fields(edgar_df):
    q1 = edgar_df.loc[("Q", pd.Timestamp("2024-03-31"))]
    assert q1["ebitda"] == 35
    assert q1["total_debt"] == 350
    assert q1["shares_outstanding"] == 7e6


def test_edgar_empty_payload():
    assert companyfacts_to_financials({"facts": {}}).empty


def test_sec_client_requires_contact():
    with pytest.raises(ValueError, match="contact email"):
        SecClient("TradeMare")


def test_edgar_cik_lookup_prefers_universe_mapping():
    class NoNetwork(SecClient):
        def get_json(self, url, retries=3):
            return {"0": {"ticker": "BRK-B", "cik_str": 1067983}}

    provider = EdgarFundamentalsProvider(NoNetwork("x a@b.c"), {"AAPL": "0000320193"})
    assert provider.cik_for("AAPL") == "0000320193"
    assert provider.cik_for("BRK.B") == "0001067983"
    assert provider.cik_for("ZZZZ") is None


def test_yf_symbol_mapping():
    assert yf_symbol(Market.IN, "M&M") == "M&M.NS"
    assert yf_symbol(Market.US, "BRK.B") == "BRK-B"


def test_yfinance_statements_mapping():
    ends = [pd.Timestamp("2025-03-31"), pd.Timestamp("2024-03-31")]
    income = pd.DataFrame(
        {ends[0]: [100.0, 10.0], ends[1]: [90.0, 9.0]}, index=["Total Revenue", "Net Income"]
    )
    balance = pd.DataFrame({ends[0]: [500.0]}, index=["Total Assets"])
    cash = pd.DataFrame({ends[0]: [-7.0]}, index=["Capital Expenditure"])
    df = statements_to_financials(Market.IN, {"A": [income, balance, cash], "Q": [pd.DataFrame()]})
    latest = df.set_index("period_end").loc[ends[0]]
    assert latest["revenue"] == 100 and latest["net_income"] == 10
    assert latest["capex"] == 7  # sign normalised
    assert latest["available_from"] == ends[0] + pd.Timedelta(days=60)  # SEBI annual deadline
    assert len(df) == 2


def test_universe_parsers():
    nifty = _parse_nifty(
        "Company Name,Industry,Symbol,Series,ISIN Code\n"
        "Infosys Ltd.,Information Technology,INFY,EQ,INE009A01021\n"
        "Some Co,Services,SOME,BE,X\n"
    )
    assert nifty["symbol"].tolist() == ["INFY"] and nifty["sector"].iloc[0] == "Information Technology"
    sp = _parse_sp500("Symbol,Security,GICS Sector,CIK\nAAPL,Apple Inc.,Information Technology,320193\n")
    assert sp["cik"].iloc[0] == "0000320193"
