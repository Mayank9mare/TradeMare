# ruff: noqa: RUF001, E501
import json
import math
from datetime import date

import pandas as pd
import pytest

from trademare.analysis.ipo import build_card, ipo_alerts, ipo_facts, retail_odds
from trademare.analysis.ipo_study import (
    BucketStats,
    Outcome,
    StudyResult,
    bucket_of,
    label_for,
    listing_outcome,
    spearman,
    summarize,
)
from trademare.data.ipo import (
    Ipo,
    IpoService,
    IssueFacts,
    Subscription,
    issue_kind,
    parse_facts,
    parse_nasdaq,
    parse_past,
    parse_subscription,
    parse_upcoming,
    price_band,
)

UPCOMING = [
    {"companyName": "Bonfiglioli Transmissions Limited", "issueEndDate": "19-Oct-2026",
     "issuePrice": "Rs.558 to Rs.586", "issueSize": "30282330", "issueStartDate": "15-Oct-2026",
     "series": "EQ", "status": "Active", "symbol": "BONFIG"},
    {"companyName": "Jio Platforms Limited", "issueEndDate": "23-Oct-2026", "issuePrice": "Rs.1065 to Rs.1119",
     "issueSize": "197525934", "issueStartDate": "21-Oct-2026", "series": "EQ", "status": "Forthcoming",
     "symbol": "JPL"},
]  # fmt: skip
PAST = [
    {"company": "HD Fire Protect Limited", "ipoEndDate": "15-OCT-2026", "ipoStartDate": "13-OCT-2026",
     "issuePrice": "-", "listingDate": "-", "priceRange": "Rs.258 to Rs.271", "securityType": "EQ",
     "symbol": "HDFIRE"},
    {"companyName": "Vaibhav Vyapaar Limited", "ipoEndDate": "15-OCT-2026", "ipoStartDate": "13-OCT-2026",
     "issuePrice": "-", "listingDate": "-", "priceRange": "Rs.51 to Rs.54", "securityType": "SME",
     "symbol": "LOANFRONT"},
    {"company": "German Green Steel and Power Limited", "ipoEndDate": "29-SEP-2026",
     "ipoStartDate": "25-SEP-2026", "issuePrice": "   139", "listingDate": "05-OCT-2026",
     "priceRange": "Rs.132 to Rs.139", "securityType": "EQ", "symbol": "GERMAN"},
]  # fmt: skip
DETAIL = {
    "issueInfo": {
        "dataList": [
            {"title": "Issue Size", "value": '"Initial Public Offer of offer for sale up to 4,32,39,042 Equity Shares"'},
            {"title": "Bid Lot", "value": "25 Equity Shares and in multiples thereof"},
            {"title": "Book Running Lead Managers", "value": '"Axis Capital Limited and BNP Paribas"'},
            {"title": "Red Herring Prospectus", "value": "https://nsearchives.nseindia.com/content/ipo/RHP_BONFIG.zip"},
            {"title": None, "value": "boilerplate"},
        ]
    }
}  # fmt: skip


def sub_payload(qib, nii, retail, total):
    return {
        "dataList": [
            {"category": "Category", "noOfTotalMeant": "No. of times", "srNo": "Sr.No."},
            {"category": "Qualified Institutional Buyers(QIBs)", "noOfTotalMeant": str(qib), "srNo": "1"},
            {"category": "Mutual funds", "noOfTotalMeant": "", "srNo": "1(c)"},
            {"category": "Non Institutional Investors", "noOfTotalMeant": str(nii), "srNo": "2"},
            {"category": "Non Institutional Investors(Bid amount of more than Ten Lakh Rupees)",
             "noOfTotalMeant": "46.4", "srNo": "2.1"},
            {"category": "Retail Individual Investors(RIIs)", "noOfTotalMeant": str(retail), "srNo": "3"},
            {"category": "Total", "noOfTotalMeant": str(total), "srNo": None},
        ],
        "updateTime": "Updated as on 19-Oct-2026 19:00:00",
    }  # fmt: skip


# ---------------------------------------------------------------- parsing


def test_price_band_and_kind():
    assert price_band("Rs.558 to Rs.586") == (558, 586)
    assert price_band("Rs.1,000") == (1000, 1000)
    assert price_band("-") == (None, None)
    assert issue_kind("Fresh Issue of up to X and an Offer for Sale of Y") == "fresh+ofs"
    assert issue_kind("offer for sale up to") == "ofs"
    assert issue_kind("Fresh issue of") == "fresh"


def test_parse_upcoming_and_past():
    up = parse_upcoming(UPCOMING)
    assert [i.symbol for i in up] == ["BONFIG", "JPL"]
    b = up[0]
    assert (b.start, b.end, b.price_low, b.price_high) == (date(2026, 10, 15), date(2026, 10, 19), 558, 586)
    assert b.issue_value == pytest.approx(30282330 * 586)
    past = parse_past(PAST)
    assert [i.symbol for i in past] == ["HDFIRE", "GERMAN"]  # SME left out
    assert past[0].issue_price is None and past[0].listing_date is None
    assert past[1].issue_price == 139 and past[1].listing_date == date(2026, 10, 5)


def test_parse_facts_and_subscription():
    f = parse_facts(DETAIL)
    assert (f.lot, f.kind) == (25, "ofs")
    assert f.lead_managers == "Axis Capital Limited and BNP Paribas"
    assert f.prospectus_url.endswith("RHP_BONFIG.zip")
    s = parse_subscription(sub_payload(120.8, 40.4, 5.9, 46.1))
    assert (s.qib, s.nii, s.retail, s.total) == (120.8, 40.4, 5.9, 46.1)  # sub-rows like 2.1 ignored
    assert s.as_of == "19-Oct-2026 19:00:00"
    assert parse_subscription({"dataList": []}) is None


def test_status_and_money():
    ipo = Ipo("IN", "X", "X Ltd", date(2026, 10, 15), date(2026, 10, 19), 558, 586, 1000,
              facts=IssueFacts(lot=25))  # fmt: skip
    assert ipo.status(date(2026, 10, 14)) == "upcoming"
    assert ipo.status(date(2026, 10, 19)) == "open"
    assert ipo.status(date(2026, 10, 20)) == "closed"
    ipo.listing_date = date(2026, 10, 22)
    assert ipo.status(date(2026, 10, 22)) == "listed"
    assert ipo.min_investment == 25 * 586
    ipo.issue_price = 580
    assert ipo.min_investment == 25 * 580  # the final price once fixed


def test_parse_nasdaq():
    payload = {"data": {
        "priced": {"rows": [{"proposedTickerSymbol": "CTY", "companyName": "CITY THERAPEUTICS, INC.",
                             "proposedExchange": "NASDAQ Global", "proposedSharePrice": "18.00",
                             "sharesOffered": "11,111,111", "pricedDate": "10/16/2026"}]},
        "upcoming": {"upcomingTable": {"rows": [{"proposedTickerSymbol": "NEW", "companyName": "NEW CO",
                     "proposedSharePrice": "14.00-16.00", "sharesOffered": "1,000,000",
                     "expectedPriceDate": "10/22/2026"}]}},
    }}  # fmt: skip
    ipos = {i.symbol: i for i in parse_nasdaq(payload)}
    assert ipos["CTY"].issue_price == 18 and ipos["CTY"].extra["deal"] == "priced"
    assert ipos["NEW"].price_low == 14 and ipos["NEW"].price_high == 16 and ipos["NEW"].issue_price is None
    assert parse_nasdaq({"data": {"upcoming": {"upcomingTable": {"rows": None}}}}) == []


# ---------------------------------------------------------------- service (cached, no network)


class FakeNse:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get_json(self, url):
        self.calls.append(url)
        for key, payload in self.routes.items():
            if key in url:
                return payload
        raise RuntimeError("HTTP 404")


def test_india_merges_lists_and_fetches_subscription_only_once_bidding_started(tmp_path):
    nse = FakeNse({
        "all-upcoming-issues": UPCOMING,
        "public-past-issues": PAST,
        "ipo-detail": DETAIL,
        "ipo-active-category?symbol=BONFIG": sub_payload(81.3, 30.4, 4.1, 31.8),
        "ipo-active-category?symbol=HDFIRE": sub_payload(256.7, 30.5, 30.5, 131.7),
        "ipo-active-category?symbol=GERMAN": sub_payload(20.9, 22.8, 22.8, 29.0),
    })  # fmt: skip
    svc = IpoService(tmp_path, nse=nse)
    ipos = {i.symbol: i for i in svc.india(date(2026, 10, 19))}
    assert set(ipos) == {"BONFIG", "JPL", "HDFIRE", "GERMAN"}
    assert ipos["JPL"].subscription is None  # not open yet
    assert not any("symbol=JPL" in u and "active-category" in u for u in nse.calls)
    assert ipos["BONFIG"].subscription.qib == 81.3 and ipos["BONFIG"].facts.lot == 25
    assert ipos["GERMAN"].status(date(2026, 10, 19)) == "listed"
    # a second call is served from the cache
    n = len(nse.calls)
    svc.india(date(2026, 10, 19))
    assert len(nse.calls) == n
    # on failure the stale cache is used rather than raising
    svc2 = IpoService(tmp_path, nse=FakeNse({}))
    (tmp_path / "ipo" / "nse-upcoming.json").touch()
    assert {i.symbol for i in svc2.upcoming()} == {"BONFIG", "JPL"}


# ---------------------------------------------------------------- study


def test_bucket_edges():
    assert bucket_of(None) is None
    assert bucket_of(0.5) == "< 1×"
    assert bucket_of(1.0) == "1–10×"
    assert bucket_of(49.99) == "10–50×"
    assert bucket_of(100) == "≥ 100×"


def bars(index, opens, closes, splits=None):
    df = pd.DataFrame({"Open": opens, "Close": closes}, index=pd.to_datetime(index))
    if splits is not None:
        df["Stock Splits"] = splits
    return df


def test_listing_outcome_undoes_later_splits_and_needs_a_bar_near_listing():
    b = bars(["2024-01-10", "2024-01-11", "2024-06-03"], [60, 61, 30], [65, 62, 31], [0, 0, 2.0])
    o = listing_outcome("X", date(2024, 1, 5), 80.0, 100.0, date(2024, 1, 10), b)
    # Yahoo shows pre-split prices halved (2:1 split later), so the issue price is halved too
    assert o.gain_close == pytest.approx(65 / 50 - 1) and o.gain_open == pytest.approx(60 / 50 - 1)
    assert o.six_month is None  # not 126 sessions yet
    late = bars(["2024-01-20"], [60], [65])
    assert listing_outcome("X", date(2024, 1, 5), 80.0, 100.0, date(2024, 1, 10), late) is None


def test_six_month_return_vs_nifty():
    days = pd.bdate_range("2024-01-10", periods=130)
    b = pd.DataFrame({"Open": 100.0, "Close": [110.0] * 126 + [121.0] * 4}, index=days)
    nifty = pd.Series([1000.0] * 126 + [1100.0] * 4, index=days)
    o = listing_outcome("X", date(2024, 1, 5), 5.0, 100.0, date(2024, 1, 10), b, nifty)
    assert o.six_month == pytest.approx(0.21) and o.nifty_six_month == pytest.approx(0.10)


def test_spearman_and_label_rule():
    r, p, n = spearman(pd.Series(range(50)), pd.Series(range(50)))
    assert r == pytest.approx(1.0) and p == 0.0 and n == 50
    noise = pd.Series([math.sin(i * 7.3) for i in range(60)])
    assert spearman(pd.Series(range(60)), noise)[1] > 0.05
    mk = lambda share, med: BucketStats("x", 10, med, share, None, None, 0)  # noqa: E731
    assert label_for(mk(0.80, 0.12)) == "Favourable"
    assert label_for(mk(0.75, 0.10)) == "Favourable"  # boundaries are inclusive
    assert label_for(mk(0.70, 0.12)) == "Mixed"
    assert label_for(mk(0.59, 0.12)) == "Unfavourable"
    assert label_for(mk(0.80, -0.01)) == "Unfavourable"
    assert label_for(None) is None


def outcomes(rising: bool):
    out = []
    for k in range(40):
        qib = float(k * 5)
        gain = (k / 40 if rising else math.sin(k * 7.3)) - 0.1
        end = date(2018, 1, 1) if k % 2 else date(2024, 1, 1)
        out.append(Outcome("S", end, qib, 100, 100, 100 * (1 + gain), gain, gain))
    return out


def test_summarize_requires_both_halves():
    assert summarize(outcomes(True), {})["h1_holds"] is True
    s = summarize(outcomes(False), {})
    assert s["h1_holds"] is False
    assert sum(b["count"] for b in s["buckets"]) == 40


# ---------------------------------------------------------------- cards and alerts


def study(h1=True):
    good = BucketStats("≥ 100×", 30, 0.35, 0.93, 0.30, 0.02, 25)
    weak = BucketStats("1–10×", 50, 0.01, 0.55, 0.02, -0.08, 40)
    return StudyResult(h1, {"≥ 100×": good, "1–10×": weak}, "2026-10-19", 80)


def open_ipo(qib=120.0, retail=8.0, kind="ofs"):
    return Ipo("IN", "BONFIG", "Bonfiglioli", date(2026, 10, 15), date(2026, 10, 19), 558, 586, 3e7,
               facts=IssueFacts(lot=25, kind=kind),
               subscription=Subscription(qib=qib, nii=30, retail=retail, total=46))  # fmt: skip


def test_card_label_only_when_study_passed():
    c = build_card(open_ipo(), date(2026, 10, 19), study(True))
    assert (c.bucket, c.label) == ("≥ 100×", "Favourable")
    assert c.retail_odds == pytest.approx(1 / 8)
    assert any("none of the money" in n for n in c.notes) and any("running count" in n for n in c.notes)
    assert build_card(open_ipo(qib=5), date(2026, 10, 19), study(True)).label == "Unfavourable"
    no = build_card(open_ipo(), date(2026, 10, 19), study(False))
    assert no.label is None and no.stats.count == 30  # the track record is still shown
    assert build_card(open_ipo(), date(2026, 10, 19), None).label is None
    assert retail_odds(0.8) is None and retail_odds(1.4) is None and retail_odds(2.0) == 0.5
    facts = ipo_facts(c)
    assert facts["lot_shares"] == 25 and facts["subscription_times"]["qib"] == 120.0
    json.dumps(facts, default=str)


def test_alerts_open_soon_and_closing_afternoon():
    upcoming = Ipo("IN", "JPL", "Jio", date(2026, 10, 21), date(2026, 10, 23), 1065, 1119,
                   facts=IssueFacts(lot=12))  # fmt: skip
    cards = [
        build_card(upcoming, date(2026, 10, 19), study()),
        build_card(open_ipo(), date(2026, 10, 19), study()),
    ]
    morning = dict(ipo_alerts(cards, date(2026, 10, 19), 10))
    assert set(morning) == {"ipo-opens:JPL"}  # closing-day alert waits for the afternoon figures
    assert "lot 12 (₹13,428)" in morning["ipo-opens:JPL"]
    afternoon = dict(ipo_alerts(cards, date(2026, 10, 19), 14))
    text = afternoon["ipo-closes:BONFIG"]
    assert "QIB 120.0×" in text and "median listing-day gain +35%" in text and "Rule: Favourable" in text
    far = build_card(upcoming, date(2026, 10, 15), study())
    assert ipo_alerts([far], date(2026, 10, 15), 10) == []


# ---------------------------------------------------------------- page


def test_ipo_page_renders_from_cache(tmp_path, monkeypatch):
    from tests.test_web_trading import make_app
    from trademare.data.ipo import ist_now

    today = ist_now().date()
    fmt = lambda d: d.strftime("%d-%b-%Y")  # noqa: E731
    cache = tmp_path / "cache" / "ipo"
    cache.mkdir(parents=True)
    up = [dict(UPCOMING[0], issueStartDate=fmt(today), issueEndDate=fmt(today), symbol="BONFIG")]
    (cache / "nse-upcoming.json").write_text(json.dumps(up))
    (cache / "nse-past.json").write_text("[]")
    (cache / "detail-BONFIG.json").write_text(json.dumps(DETAIL))
    (cache / "sub-BONFIG.json").write_text(json.dumps(sub_payload(81.3, 30.4, 4.1, 31.8)))
    (cache / f"nasdaq-{today:%Y-%m}.json").write_text(json.dumps({"data": {}}))
    client, _ = make_app(tmp_path, monkeypatch)
    page = client.get("/ipos")
    assert page.status_code == 200
    assert "Bonfiglioli Transmissions Limited" in page.text and "81.3×" in page.text
    assert "closes today" in page.text and "Offer for sale" in page.text
    assert "trademare ipo study" in page.text  # no study on this machine yet
    assert 'href="/ipos"' in client.get("/").text
