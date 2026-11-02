from datetime import UTC, date, datetime
from types import SimpleNamespace

from tests.test_whatsapp import Session, configured
from trademare.core.models import Market
from trademare.notify import cards, phone, telegram, whatsapp
from trademare.trading.desk import DailyReport

EXPIRES = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)


def sig(symbol, qty, price, stop, action="ENTRY", **kw):
    base = dict(reasons={}, strategy=None, exit_reason=None, direction="LONG", expires_at=EXPIRES)
    return SimpleNamespace(
        symbol=symbol, quantity=qty, entry_price=price, stop_price=stop, action=action, **{**base, **kw}
    )


def report():
    r = DailyReport(market=Market.IN, session=date(2026, 10, 30))
    r.signals = [
        sig("HSCL", 13, 509.2, 437.05, strategy="setup:vcp"),
        sig("NALCO", 30, 260.0, 238.0, reasons={"events": ["Results due 2026-11-06 (4 sessions away)"]}),
        sig("TATAPOWER", 20, 395.0, None, action="EXIT", exit_reason="signal"),
    ]
    r.fills = ["MCX: LONG 2 @ 8,120.50 (broker)", "ABB: closed (stop) @ 5,010.00, P&L -1,240.00"]
    r.orders_ended = [
        "Buy 6 GESHIP expired on Fri 30 Oct without filling: the lowest price, ₹1,600.00, "
        "stayed above your limit ₹1,593.60."
    ]
    r.notes = ["HEADS-UP HDFCBANK (held): results due 2026-11-02; stop 950.00", "ALERT ITC: stop hit"]
    r.equity, r.previous_equity, r.open_positions = 100250.0, 99850.0, 4
    return r


def ipo(symbol, status, start, end, qib=None, label=None):
    issue = SimpleNamespace(
        symbol=symbol, start=start, end=end, price_low=78.0, price_high=82.0, price=82.0,
        min_investment=14924.0, listing_date=None,
    )  # fmt: skip
    return SimpleNamespace(ipo=issue, status=status, qib=qib, label=label, listing_gain=None)


def test_daily_block_tables():
    closes = {"HSCL": 505.1, "NALCO": 262.4, "TATAPOWER": 1395.0}
    b = cards.daily_block(report(), telegram.ALERT_PREFIXES, telegram.HEADS_UP, closes)
    assert b.title == "TradeMare · India" and b.subtitle == "after Fri 30 Oct"
    assert b.urgent == ["ITC: stop hit"]
    decide, happened = b.tables
    assert decide.title == "Decide by Mon 2 Nov, 3:30 pm"
    assert decide.rows == [
        ["HSCL · VCP", "13", "₹505.10", "above ₹509.20", "₹437.05", "₹938"],
        ["NALCO ⚠", "30", "₹262.40", "₹260.00", "₹238.00", "₹660"],
        ["TATAPOWER · SELL", "20", "₹1,395", "sell at open", "—", "—"],
    ]
    assert decide.note.startswith("All buys together ₹14,420")
    assert "NALCO results Fri 6 Nov" in decide.note and "TATAPOWER: fell out of the top ranks" in decide.note
    assert happened.rows == [
        ["MCX", "Bought 2 at ₹8,120", ""],
        ["ABB", "Stop hit, sold at ₹5,010", cards.MINUS + "₹1,240"],
        ["GESHIP", "Not filled: price stayed above your limit", ""],
        ["HDFCBANK", "Results next session (you hold it)", ""],
    ]
    assert b.portfolio == ("₹1,00,250", "+0.4%", cards.GREEN, "4")
    assert (
        cards.daily_block(DailyReport(market=Market.IN, session=date(2026, 10, 30)), ("ALERT",), "H") is None
    )


def test_ipo_table_and_caption():
    today = date(2026, 11, 2)
    listed = ipo("OLD", "listed", date(2026, 10, 20), date(2026, 10, 22))
    closing = ipo("BCSSL", "open", date(2026, 10, 29), today, qib=35.5, label="Mixed")
    soon = ipo("NEWCO", "upcoming", date(2026, 11, 4), date(2026, 11, 6))
    later = ipo("LATER", "upcoming", date(2026, 11, 20), date(2026, 11, 24))
    t = cards.ipo_table([listed, closing, soon, later], today)
    assert [r[:2] for r in t.rows] == [["BCSSL", "Closes today"], ["NEWCO", "Opens Wed 4 Nov"]]
    assert t.rows[0][2:] == [f"₹78{cards.EN_DASH}82", "₹14,924", "35.5x", "Mixed"]
    after = cards.ipo_table([closing, soon], today, after_close=True)
    assert [r[0] for r in after.rows] == ["NEWCO"]  # bidding already over at 5 pm
    assert cards.ipo_table([listed, later], today) is None
    r = report()
    b = cards.daily_block(r, telegram.ALERT_PREFIXES, telegram.HEADS_UP)
    caption = cards.daily_caption([b], t, [r])
    assert caption == (
        "India: 1 urgent, 3 decisions due Mon 2 Nov, 3:30 pm, 2 trades done, 1 not filled; "
        "2 IPOs open or opening soon"
    )
    png = cards.render_daily([b], t)
    assert png.startswith(b"\x89PNG") and len(png) > 10_000


def test_weekly_pdf_renders():
    holdings = cards.Table("Holdings", ["Stock", "Qty"], [1, 1], "lr", [["MCX", "2"]])
    w = cards.Weekly(
        Market.IN, date(2026, 10, 30), equity=100250.0, week_change=0.018, benchmark_change=0.009
    )
    w.holdings, w.coming = holdings, ["HSCL results Thu 12 Nov"]
    assert w.summary() == "account +1.8%, Nifty 50 +0.9%, 0 trades"
    pdf = cards.render_weekly([w])
    assert pdf.startswith(b"%PDF")


class MediaSession(Session):
    def post(self, url, json=None, headers=None, timeout=None, data=None, files=None):
        self.calls.append((url, json, data, files))
        body = {"id": "media-1"} if url.endswith("/media") else self.body
        return type("R", (), {"status_code": self.status, "json": lambda s: body, "text": str(body)})()


def test_send_media_uploads_then_sends_template():
    config, secrets = configured()
    session = MediaSession()
    n = whatsapp.send_media(
        config, secrets, "trademare_daily_card", "image", b"png", "image/png", "d.png",
        ["line one\nline two"], session=session,
    )  # fmt: skip
    assert n == 1
    upload, send = session.calls
    assert upload[0].endswith("/1234567890/media") and upload[2]["type"] == "image/png"
    template = send[1]["template"]
    assert template["name"] == "trademare_daily_card"
    header, body = template["components"]
    assert header["parameters"] == [{"type": "image", "image": {"id": "media-1"}}]
    assert body["parameters"] == [{"type": "text", "text": "line one. line two"}]  # no line breaks allowed
    doc = whatsapp.media_payload(config.notifications.whatsapp, "91", "w", "document", "m", ["a"], "w.pdf")
    assert doc["template"]["components"][0]["parameters"][0]["document"] == {"id": "m", "filename": "w.pdf"}


def test_daily_falls_back_to_text_when_card_refused(monkeypatch):
    config, secrets = configured()
    config.notifications.whatsapp.include_ipos = False

    def refuse(*a, **k):
        raise RuntimeError("HTTP 404: Template name does not exist in the translation")

    sent = []
    monkeypatch.setattr(whatsapp, "send_media", refuse)
    monkeypatch.setattr(whatsapp, "send_text", lambda c, s, text: sent.append(text) or "whatsapp sent (1)")
    assert phone.whatsapp_daily(config, secrets, [report()]) == "whatsapp sent (1)"
    assert sent and sent[0].startswith("India, after Fri 30 Oct")


def test_target_column_and_raised_stops():
    r = DailyReport(market=Market.US, session=date(2026, 10, 30))
    r.signals = [sig("MO", 10, 78.03, 74.62, target_price=84.85)]
    r.stops_raised = [("MNST", 45.28, 46.10, 49.5)]
    b = cards.daily_block(r, telegram.ALERT_PREFIXES, telegram.HEADS_UP, {"MO": 78.03})
    decide, raised = b.tables
    assert decide.columns == ["Stock", "Qty", "Price now", "Buy up to", "Stop-loss", "Target", "Max loss"]
    assert decide.rows == [["MO", "10", "$78.03", "$78.03", "$74.62", "$84.85", "$34"]]
    assert raised.title == "Raise your stop-loss"
    assert raised.rows == [["MNST", "$49.50", "$45.28", "$46.10"]]
    assert "raise 1 stop" in cards.daily_caption([b], None, [r])
    assert "Raise your stop-loss (update a Kite GTT to match): MNST from $45.28 to $46.10" in whatsapp.digest(
        [r], "http://x"
    )
    india = cards.daily_block(report(), telegram.ALERT_PREFIXES, telegram.HEADS_UP)
    assert "Target" not in india.tables[0].columns  # India's strategy sells without a target
