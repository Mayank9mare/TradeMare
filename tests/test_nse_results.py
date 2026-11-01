# ruff: noqa: E501  (XBRL and feed fixtures)
"""NSE results filings: XBRL figures, real publication dates, overlay and the post-results refresh."""

from datetime import date, datetime

import pandas as pd
import pytest

from trademare.core.models import Market
from trademare.data.fields import normalize_financials
from trademare.data.nse_results import (
    Filing,
    NseResults,
    SymbolResults,
    available_day,
    latest_result,
    overlay,
    parse_xbrl,
)

XBRL = """<xbrli:xbrl>
<xbrli:context id="OneD"><xbrli:entity/><xbrli:period><xbrli:startDate>2026-07-01</xbrli:startDate><xbrli:endDate>2026-09-30</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="FourD"><xbrli:entity/><xbrli:period><xbrli:startDate>2026-04-01</xbrli:startDate><xbrli:endDate>2026-09-30</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="OneI"><xbrli:entity/><xbrli:period><xbrli:instant>2026-09-30</xbrli:instant></xbrli:period></xbrli:context>
<xbrli:context id="Seg1D"><xbrli:entity><xbrli:segment><xbrldi:explicitMember>x</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:startDate>2026-07-01</xbrli:startDate><xbrli:endDate>2026-09-30</xbrli:endDate></xbrli:period></xbrli:context>
<in-capmkt:DateOfEndOfReportingPeriod contextRef="OneD">2026-09-30</in-capmkt:DateOfEndOfReportingPeriod>
<in-capmkt:RevenueFromOperations contextRef="Seg1D" unitRef="INR">1</in-capmkt:RevenueFromOperations>
<in-capmkt:RevenueFromOperations contextRef="FourD" unitRef="INR">15431100000</in-capmkt:RevenueFromOperations>
<in-capmkt:RevenueFromOperations contextRef="OneD" unitRef="INR">8061900000</in-capmkt:RevenueFromOperations>
<in-capmkt:ProfitOrLossAttributableToOwnersOfParent contextRef="OneD" unitRef="INR">0</in-capmkt:ProfitOrLossAttributableToOwnersOfParent>
<in-capmkt:ProfitLossForPeriod contextRef="OneD" unitRef="INR">385700000</in-capmkt:ProfitLossForPeriod>
<in-capmkt:FinanceCosts contextRef="OneD" unitRef="INR">142100000</in-capmkt:FinanceCosts>
<in-capmkt:Equity contextRef="OneI" unitRef="INR">60194600000</in-capmkt:Equity>
<in-capmkt:PaidUpValueOfEquityShareCapital contextRef="OneD" unitRef="INR">1154700000</in-capmkt:PaidUpValueOfEquityShareCapital>
<in-capmkt:FaceValueOfEquityShareCapital contextRef="OneD" unitRef="INR">1</in-capmkt:FaceValueOfEquityShareCapital>
</xbrli:xbrl>"""


def test_parse_xbrl_reads_the_quarter_without_dimensions():
    end, figs = parse_xbrl(XBRL)
    assert end == "2026-09-30"
    assert figs["revenue"] == 8061900000  # not the half-year or the segment value
    assert figs["net_income"] == 385700000  # total profit, not the 0 "attributable" line
    assert figs["interest_expense"] == 142100000 and figs["total_equity"] == 60194600000
    assert figs["shares_outstanding"] == pytest.approx(1154700000)
    assert parse_xbrl("<x/>") == (None, {})


def test_filings_after_the_daily_run_count_from_the_next_day():
    assert available_day("2026-10-29T13:31:20") == date(2026, 10, 29)
    assert available_day("2026-10-31T23:52:14") == date(2026, 11, 1)
    assert available_day(None) is None


def results(*filings, figures=None):
    return SymbolResults("ABC", "2026-11-01T10:00:00", list(filings), figures or {})


def F(q, b, cons=True, original=True, fmt="integrated"):
    return Filing(q, b, cons, original, "https://x/y.xml", fmt)


def test_overlay_uses_real_dates_and_adds_the_newest_quarter():
    yahoo = normalize_financials(pd.DataFrame([
        {"period_type": "Q", "period_end": "2026-06-30", "available_from": "2026-08-14", "revenue": 7e9, "net_income": 1e8,
         "operating_cash_flow": 5e8},
        {"period_type": "A", "period_end": "2026-03-31", "available_from": "2026-05-30", "revenue": 2.4e10, "net_income": 1e9},
        {"period_type": "Q", "period_end": "2026-03-31", "available_from": "2026-05-15", "revenue": 7e9, "net_income": 5e8},
    ]))  # fmt: skip
    res = results(
        F("2026-06-30", "2026-07-28T14:57:17"), F("2026-06-30", "2026-07-28T14:57:15", cons=False),
        F("2026-03-31", "2026-05-25T21:54:10"), F("2026-09-30", "2026-10-29T13:31:20"),
        figures={"2026-09-30": {"revenue": 8.06e9, "net_income": 3.857e8}},
    )  # fmt: skip
    out = overlay(yahoo, res).set_index(["period_type", "period_end"])
    assert out.loc[("Q", pd.Timestamp("2026-06-30")), "available_from"] == pd.Timestamp("2026-07-28")
    assert out.loc[("Q", pd.Timestamp("2026-06-30")), "operating_cash_flow"] == 5e8  # Yahoo values kept
    assert out.loc[("A", pd.Timestamp("2026-03-31")), "available_from"] == pd.Timestamp(
        "2026-05-26"
    )  # after 17:00
    new = out.loc[("Q", pd.Timestamp("2026-09-30"))]
    assert new["revenue"] == 8.06e9 and new["available_from"] == pd.Timestamp("2026-10-29")
    assert overlay(yahoo, None) is yahoo


def test_latest_result_growth():
    res = results(
        F("2026-09-30", "2026-10-29T13:31:20"), F("2025-09-30", "2025-12-03T17:23:37"),
        figures={"2026-09-30": {"revenue": 806.0, "net_income": 38.6}, "2025-09-30": {"revenue": 650.0, "net_income": 6.0}},
    )  # fmt: skip
    lr = latest_result(res)
    assert lr["published"] == "2026-10-29" and lr["revenue_growth_yoy"] == pytest.approx(
        806 / 650 - 1, abs=1e-4
    )
    assert latest_result(None) is None


class FakeNse:
    def __init__(self, pages=None, per_symbol=None):
        self.pages, self.per_symbol, self.calls = pages or [], per_symbol or {}, []
        self.session = self

    def get_json(self, url):
        self.calls.append(url)
        if "page=" in url:
            page = int(url.rsplit("page=", 1)[1])
            return {"data": self.pages[page] if page < len(self.pages) else []}
        if "integrated-filing-results" in url:
            sym = url.split("symbol=")[1].split("&")[0]
            return {"data": self.per_symbol.get(sym, [])}
        return []

    def get(self, url, timeout=30):
        class R:
            text = XBRL

            def raise_for_status(self):
                return None

        return R()


def test_recent_filers_stops_at_older_pages():
    pages = [
        [{"symbol": "AAA", "broadcast_Date": "31-Oct-2026 20:00:00"}, {"symbol": "BBB", "broadcast_Date": "30-Oct-2026 10:00:00"}],
        [{"symbol": "CCC", "broadcast_Date": "29-Oct-2026 10:00:00"}],
        [{"symbol": "DDD", "broadcast_Date": "28-Oct-2026 10:00:00"}],
    ]  # fmt: skip
    nse = FakeNse(pages)
    found = NseResults("unused", nse=nse).recent_filers(datetime(2026, 10, 30, 0, 0))
    assert found == {"AAA", "BBB"} and len([c for c in nse.calls if "page=" in c]) == 2


def test_get_caches_filings_and_figures(tmp_path):
    rows = [{"qe_Date": "30-SEP-2026", "broadcast_Date": "29-Oct-2026 13:31:20", "consolidated": "Consolidated",
             "type_Sub": "Original", "xbrl": "https://x/a.xml"}]  # fmt: skip
    svc = NseResults(tmp_path, nse=FakeNse(per_symbol={"ABC": rows}))
    res = svc.get("ABC")
    assert (
        res.figures["2026-09-30"]["revenue"] == 8061900000
        and svc.cached("ABC").filings[0].quarter_end == "2026-09-30"
    )


def test_refresh_after_results_targets_filers_and_overdue_holdings(tmp_path, monkeypatch):
    from trademare.data import results_refresh

    refreshed, forced, events_refreshed = [], [], []

    class Res:
        error = None

    class Nse:
        def recent_filers(self, since):
            return {"AAA", "ZZZ"}  # ZZZ is outside the universe and not owned

        def get(self, symbol, refresh=False):
            refreshed.append(symbol)
            return Res()

    class Ev:
        next_results = date(2026, 10, 29)

    class Events:
        def cached(self, market, symbol):
            return Ev() if symbol == "HELD" else None

        def get(self, market, symbol, today=None, refresh=False):
            events_refreshed.append(symbol)

    class Report:
        def summary(self):
            return "ok"

    class Service:
        class config:
            class data:
                cache_dir = str(tmp_path)

        store = type(
            "S",
            (),
            {
                "financials_updated_at": lambda self, m, src, s: datetime(
                    2026, 10, 26, tzinfo=None
                ).astimezone()
            },
        )()

        def nse_results(self):
            return Nse()

        def events(self):
            return Events()

        def universe_symbols(self, market):
            return ["AAA", "BBB"]

        def market(self, market):
            return type(
                "M", (), {"financials": type("F", (), {"source": type("S", (), {"source": "yfinance"})()})()}
            )()

        def update_fundamentals(self, market, symbols, force=False):
            forced.extend(symbols)
            return Report()

    out = results_refresh.refresh_after_results(
        Service(), Market.IN, {"HELD"}, now=datetime(2026, 11, 1, 18, 0)
    )
    assert set(out["refreshed"]) == {"AAA", "HELD"} and out["refreshed"][0] == "HELD"  # yours first
    assert set(forced) == {"AAA", "HELD"} and events_refreshed == ["HELD"]
    assert (tmp_path / "nse_results" / "_last_check.json").exists()
    assert results_refresh.refresh_after_results(Service(), Market.US, set()) == {"skipped": "India only"}
