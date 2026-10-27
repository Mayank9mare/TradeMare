# ruff: noqa: E501  (tables of real filing text read better unwrapped)
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from trademare.analysis.forensic import compute_forensics
from trademare.analysis.red_flags import coverage_note, red_flags
from trademare.core.models import Market
from trademare.data.events import Announcement, CorporateEvents, EventsService, NseClient, sec_announcements

TODAY = date(2026, 10, 18)


def nse(category, text, days_ago=10):
    return Announcement(TODAY - timedelta(days=days_ago), category, text, "NSE")


def flags_for(*anns):
    return red_flags(CorporateEvents("IN", "X", datetime.now(UTC), announcements=list(anns)), TODAY)


# ------------------------------------------------------------------ red flags (real NSE wording)
@pytest.mark.parametrize(
    ("category", "text", "level", "title"),
    [
        ("Corporate Insolvency Resolution Process", "X Limited has informed the Exchange about Corporate Insolvency Resolution Process", "red", "Insolvency proceedings"),
        ("Fraud/Default/Arrest", "Disclosure of fraud", "red", "Fraud, default or arrest"),
        ("Defaults on Payment of Interest/Principal", "Disclosure of defaults on payment of interest for the month ended June", "red", "Default on interest or principal"),
        ("Change in Auditors", "Resignation of M/s ABC & Co as Statutory Auditors", "red", "Auditor resigned"),
        ("Change in Auditors", "Appointment of M/s ABC & Co as Statutory Auditors", "amber", "Change of auditor"),
        ("Credit Rating", "ICRA has downgraded the long term rating to BBB", "amber", "Credit rating downgrade"),
        ("Resignation", "Resignation of the Chief Financial Officer of the company", "amber", "Key person resigned"),
        ("Action(s) taken or orders passed", "Order received from GST department", "amber", "Regulator action or order"),
        ("Updates", "Intimation of invocation of pledge on shares held by promoter", "red", "Pledged shares invoked"),
        ("Disclosure under SEBI Takeover Regulations", "Creation of pledge on 2% of shares by promoter group", "amber", "Shares pledged"),
    ],
)  # fmt: skip
def test_nse_rules(category, text, level, title):
    [flag] = flags_for(nse(category, text))
    assert (flag.level, flag.title) == (level, title)


@pytest.mark.parametrize(
    ("category", "text"),
    [
        ("Defaults on Payment of Interest/Principal", "Disclosure of defaults: NIL for the quarter"),
        ("Disclosure under SEBI Takeover Regulations", "Release of pledge on shares held by promoter"),
        ("Credit Rating", "CRISIL has reaffirmed AAA / Stable"),
        ("Resignation", "Resignation of Mr X as a Non-Executive Director due to other commitments"),
        ("Press Release", "Company wins large order"),
    ],
)
def test_routine_filings_are_not_flagged(category, text):
    assert flags_for(nse(category, text)) == []


def test_flags_are_windowed_grouped_and_red_first():
    flags = flags_for(
        nse("Action(s) taken or orders passed", "GST order", 5),
        nse("Action(s) taken or orders passed", "GST order", 20),
        nse("Action(s) taken or orders passed", "old order", 120),  # amber window is 90 days
        nse("Fraud/Default/Arrest", "arrest of MD", 150),  # red window is 180 days
        nse("Fraud/Default/Arrest", "future filing", -3),  # after asof: ignored
    )
    assert [(f.level, f.count) for f in flags] == [("red", 1), ("amber", 2)]
    assert flags[1].day == TODAY - timedelta(days=5)


def test_coverage_note():
    assert coverage_note(None, TODAY) == "corporate filings not checked"
    empty = CorporateEvents("IN", "MCX", datetime.now(UTC))
    assert "BSE" in coverage_note(empty, TODAY)
    assert coverage_note(CorporateEvents("US", "AAPL", datetime.now(UTC)), TODAY) is None
    failed = CorporateEvents("IN", "X", datetime.now(UTC), error="HTTP 403")
    assert "could not fetch" in coverage_note(failed, TODAY)


# ------------------------------------------------------------------ SEC 8-K
def test_sec_items_become_flags():
    payload = {"filings": {"recent": {
        "form": ["8-K", "8-K", "NT 10-Q", "10-Q", "8-K"],
        "filingDate": ["2026-10-10", "2026-10-01", "2026-09-20", "2026-08-01", "2025-01-01"],
        "items": ["4.02,9.01", "2.02,9.01", "", "", "4.02"],
        "accessionNumber": ["0001-26-1", "0001-26-2", "0001-26-3", "0001-26-4", "0001-25-9"],
        "primaryDocument": ["a.htm", "b.htm", "c.htm", "d.htm", "e.htm"],
    }}}  # fmt: skip
    anns = sec_announcements(payload, "320193", TODAY - timedelta(days=180))
    assert [a.category for a in anns] == ["8-K", "8-K", "NT 10-Q"]  # 10-Q skipped, 2025 filing too old
    assert anns[0].url == "https://www.sec.gov/Archives/edgar/data/320193/0001261/a.htm"
    ev = CorporateEvents("US", "AAPL", datetime.now(UTC), announcements=anns)
    titles = [(f.level, f.title) for f in red_flags(ev, TODAY)]
    assert ("red", "Prior financial statements can no longer be relied on") in titles
    assert ("red", "Late periodic filing") in titles
    assert all("Results of operations" not in t for _, t in titles)  # 2.02 is routine


# ------------------------------------------------------------------ NSE client + cache
class FakeNse(NseClient):
    def __init__(self, fail=False):
        super().__init__(pause=0)
        self.fail, self.calls = fail, 0

    def get_json(self, url):
        self.calls += 1
        if self.fail:
            raise RuntimeError("NSE returned HTTP 403")
        if "board-meetings" in url:
            return [
                {"bm_date": "23-Oct-2026", "bm_purpose": "Board Meeting Intimation", "bm_desc": "to approve financial results"},
                {"bm_date": "30-Oct-2026", "bm_purpose": "Fund raising", "bm_desc": "issue of NCDs"},
                {"bm_date": "10-Jul-2026", "bm_purpose": "Financial Results", "bm_desc": "past"},
            ]  # fmt: skip
        return [
            {"an_dt": "13-Oct-2026 12:26:48", "desc": "Credit Rating", "attchmntText": "rating downgraded", "attchmntFile": "u"},
            {"an_dt": "01-Jan-2020 10:00:00", "desc": "Updates", "attchmntText": "old", "attchmntFile": None},
        ]  # fmt: skip


@pytest.mark.live_events  # exercises the real _fetch (with a fake NSE), not the conftest stub
def test_events_service_fetches_caches_and_keeps_last_good_copy(tmp_path, monkeypatch):
    monkeypatch.setattr("trademare.data.events.yahoo_next_earnings", lambda *a: None)
    nse_ok = FakeNse()
    svc = EventsService(tmp_path, nse=nse_ok)
    ev = svc.get(Market.IN, "INFY", TODAY)
    assert ev.next_results == date(2026, 10, 23) and ev.results_source == "NSE board meeting"
    assert [a.category for a in ev.announcements] == ["Credit Rating"]  # 2020 filing outside lookback
    svc.get(Market.IN, "INFY", TODAY)
    assert nse_ok.calls == 2  # second call served from the cache
    stale = EventsService(tmp_path, nse=FakeNse(fail=True)).get(Market.IN, "INFY", TODAY, refresh=True)
    assert stale.announcements and stale.error.startswith("stale:")


class SilentNse(FakeNse):  # a BSE-only filer: NSE has nothing
    def get_json(self, url):
        self.calls += 1
        return []


@pytest.mark.live_events
def test_bse_only_filer_falls_back_to_news_headlines(tmp_path, monkeypatch):
    from trademare.analysis.news import Headline

    monkeypatch.setattr("trademare.data.events.yahoo_next_earnings", lambda *a: date(2026, 10, 29))
    seen = []

    def fake_headlines(market, company, days, n):
        seen.append(company)
        return [
            Headline("MCX shares jump on record volumes", "ET", datetime(2026, 10, 15, tzinfo=UTC)),
            Headline("SEBI imposes penalty of Rs 25 lakh on MCX", "Mint", datetime(2026, 9, 30, tzinfo=UTC)),
        ]

    monkeypatch.setattr("trademare.analysis.news.fetch_headlines", fake_headlines)
    svc = EventsService(tmp_path, nse=SilentNse(), names={"MCX": "Multi Commodity Exchange of India Ltd."})
    ev = svc.get(Market.IN, "MCX", TODAY)
    assert seen == ["Multi Commodity Exchange of India Ltd."] and ev.results_source == "Yahoo"
    assert [a.source for a in ev.announcements] == ["News", "News"]
    [flag] = red_flags(ev, TODAY)
    assert (flag.level, flag.title) == ("amber", "In the news: regulator order or penalty")  # never red
    assert "news headlines were checked" in coverage_note(ev, TODAY)
    # without a known company name there is nothing to search for
    bare = EventsService(tmp_path / "b", nse=SilentNse()).get(Market.IN, "MCX", TODAY)
    assert bare.announcements == [] and "red-flag check unavailable" in coverage_note(bare, TODAY)


@pytest.mark.parametrize(
    ("headline", "title"),
    [
        ("NCLT admits insolvency plea against X Ltd", "insolvency or bankruptcy"),
        ("X Ltd defaults on bond repayment", "default on payments"),
        ("Income tax raids at X offices", "fraud or raid"),
        ("Statutory auditor of X resigns citing lack of information", "auditor resigned"),
        ("ICRA downgrades X's rating to BBB", "credit rating downgrade"),
        ("X CFO resigns amid probe", "CEO or CFO quit"),
    ],
)
def test_news_rules(headline, title):
    ann = Announcement(TODAY - timedelta(days=3), "News headline", headline, "News")
    [flag] = red_flags(CorporateEvents("IN", "X", datetime.now(UTC), announcements=[ann]), TODAY)
    assert flag.title == f"In the news: {title}" and flag.level == "amber"


def test_routine_news_is_not_flagged():
    anns = [
        Announcement(TODAY, "News headline", t, "News")
        for t in (
            "X Q2 profit rises 20%",
            "X wins order worth Rs 500 crore",
            "Credit default swap market explained",
        )
    ]
    assert red_flags(CorporateEvents("IN", "X", datetime.now(UTC), announcements=anns), TODAY) == []


# ------------------------------------------------------------------ forensic scores
def statements(**overrides):
    """Two fiscal years; year t improves on year t-1 everywhere."""
    p = dict(revenue=1000, gross_profit=400, operating_income=150, net_income=100, operating_cash_flow=90,
             total_assets=2000, total_liabilities=1000, current_assets=800, current_liabilities=500,
             total_equity=1000, total_debt=500, shares_outstanding=100, receivables=150, ppe=600,
             depreciation=60, sga=100, retained_earnings=600)  # fmt: skip
    t = dict(revenue=1200, gross_profit=500, operating_income=200, net_income=140, operating_cash_flow=180,
             total_assets=2100, total_liabilities=950, current_assets=900, current_liabilities=480,
             total_equity=1150, total_debt=450, shares_outstanding=100, receivables=170, ppe=620,
             depreciation=65, sga=115, retained_earnings=720) | overrides  # fmt: skip
    rows = []
    for year, vals in ((2025, p), (2026, t)):
        end = pd.Timestamp(f"{year}-03-31")
        rows.append(
            {"period_type": "A", "period_end": end, "available_from": end + pd.Timedelta(days=60)} | vals
        )
    return pd.DataFrame(rows)


def test_piotroski_perfect_score_and_point_in_time():
    f = compute_forensics(statements(), date(2026, 6, 30), "Industrials")
    assert f.piotroski == 9 and f.piotroski_tests == 9
    assert compute_forensics(statements(), date(2026, 5, 1), "Industrials") is None  # FY26 not public yet


def test_piotroski_failures_are_counted():
    f = compute_forensics(statements(operating_cash_flow=-10, shares_outstanding=120), date(2026, 6, 30))
    assert not f.piotroski_detail["cfo_positive"] and not f.piotroski_detail["no_dilution"]
    assert not f.piotroski_detail["cash_beats_profit"] and f.piotroski == 6


def test_altman_and_beneish_match_hand_calculation():
    f = compute_forensics(statements(), date(2026, 6, 30), "Industrials")
    ta = 2100
    z = 6.56 * (900 - 480) / ta + 3.26 * 720 / ta + 6.72 * 200 / ta + 1.05 * 1150 / 950
    assert f.altman_z == pytest.approx(round(z, 2)) and f.altman_zone == "safe"
    dsri = (170 / 1200) / (150 / 1000)
    gmi = (400 / 1000) / (500 / 1200)
    aqi = (1 - (900 + 620) / 2100) / (1 - (800 + 600) / 2000)
    sgi = 1.2
    depi = (60 / (60 + 600)) / (65 / (65 + 620))
    sgai = (115 / 1200) / (100 / 1000)
    tata = (140 - 180) / 2100
    lvgi = (950 / 2100) / (1000 / 2000)
    m = (
        -4.84
        + 0.92 * dsri
        + 0.528 * gmi
        + 0.404 * aqi
        + 0.892 * sgi
        + 0.115 * depi
        - 0.172 * sgai
        + 4.679 * tata
        - 0.327 * lvgi
    )
    assert f.beneish_m == pytest.approx(round(m, 2)) and f.beneish_flag is (m > -1.78)


def test_financials_skip_altman_and_beneish():
    f = compute_forensics(statements(), date(2026, 6, 30), "Financial Services")
    assert f.piotroski is not None and f.altman_z is None and f.beneish_m is None
    assert any("financial companies" in n for n in f.notes)


def test_zero_assets_do_not_crash():
    f = compute_forensics(statements(total_assets=0), date(2026, 6, 30), "Industrials")
    assert f.beneish_m is None and f.altman_z is None


def test_one_off_gains_do_not_count_as_profit():
    # Reported profit comes from a one-off gain; recurring income is a loss.
    stmts = statements(net_income=500)
    stmts["net_income_normalized"] = [-50, -80]
    f = compute_forensics(stmts, date(2026, 6, 30), "Telecommunication")
    assert f.piotroski_detail["roa_positive"] is False
    assert f.piotroski_detail["cash_beats_profit"] is True


def test_debenture_maturity_suspension_is_not_a_red_flag():
    from datetime import date

    from trademare.analysis.red_flags import red_flags
    from trademare.data.events import Announcement, CorporateEvents

    ncd = Announcement(
        day=date(2026, 8, 4), category="Suspension of Trading", source="NSE",
        text="Trading in Non-Convertible Debentures of Shriram Finance Limited shall be suspended "
        "w.e.f. August 05, 2026 on account of Maturity.",
    )  # fmt: skip
    shares = Announcement(
        day=date(2026, 8, 4), category="Suspension of Trading", source="NSE",
        text="Trading in equity shares of XYZ Limited shall be suspended for non-compliance.",
    )  # fmt: skip
    from datetime import UTC, datetime

    from trademare.core.models import Market

    events = CorporateEvents(
        market=Market.IN, symbol="X", fetched_at=datetime.now(UTC), announcements=[ncd, shares]
    )
    titles = [(f.title, f.detail[:30]) for f in red_flags(events, date(2026, 8, 10))]
    assert titles == [("Trading suspended", "Trading in equity shares of XY")]
