from pydantic import SecretStr

from tests.conftest import REPO_CONFIG
from trademare.config import Secrets, load_config
from trademare.notify import phone, whatsapp


def configured(mode="template"):
    config = load_config(REPO_CONFIG).model_copy(deep=True)
    wa = config.notifications.whatsapp
    wa.enabled, wa.to, wa.phone_number_id, wa.mode = True, ["+919812345678"], "1234567890", mode
    return config, Secrets(_env_file=None, whatsapp_token=SecretStr("tok"))


class Session:
    def __init__(self, status=200, body=None):
        self.calls, self.status, self.body = [], status, body or {"messages": [{"id": "wamid.1"}]}

    def post(self, url, json, headers, timeout):
        self.calls.append((url, json, headers))
        return type(
            "R", (), {"status_code": self.status, "json": lambda s: self.body, "text": str(self.body)}
        )()


def test_flatten_and_chunks():
    text = "<b>India, after Fri 30 Oct</b>\nBought 13 HSCL.\n\n  results   soon &amp; more"
    assert whatsapp.flatten(text) == "India, after Fri 30 Oct. Bought 13 HSCL. results soon & more."
    long = "\n".join(f"line {i} " + "x" * 90 for i in range(30))
    parts = whatsapp.chunks(long)
    assert len(parts) > 1 and all(len(p) <= whatsapp.MAX_PARAM for p in parts)
    assert " ".join(parts).count("line ") == 30  # nothing lost
    assert "\n" not in "".join(parts) and not any(p.endswith(".") for p in parts)  # template adds it


def test_daily_digest_is_plain_sentences():
    from datetime import UTC, date, datetime
    from types import SimpleNamespace

    from trademare.core.models import Market
    from trademare.trading.desk import DailyReport

    def sig(symbol, qty, price, stop, action="ENTRY", **kw):
        base = dict(reasons={}, strategy=None, exit_reason=None, direction="LONG")
        expires = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
        return SimpleNamespace(
            symbol=symbol,
            quantity=qty,
            entry_price=price,
            stop_price=stop,
            action=action,
            expires_at=expires,
            **{**base, **kw},
        )

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
    lines = whatsapp.digest([r], "http://x").splitlines()
    assert lines == [
        "India, after Fri 30 Oct",
        "🚨 ITC: stop hit",
        "Done: bought 2 MCX at ₹8,120.50; sold ABB (stop) at ₹5,010.00, loss ₹1,240",
        "Not filled: GESHIP (price never came down to your limit)",
        "Results next session for your holding HDFCBANK (big moves possible)",
        "2 new buy ideas to approve or skip by Mon 2 Nov, 3:30 pm: HSCL (VCP setup), NALCO (results soon)."
        " Together ₹14,420, at most ₹1,598 at risk",
        "Sell suggested by Mon 2 Nov, 3:30 pm: TATAPOWER (fell out of the top ranks)",
        "Paper portfolio ₹1,00,250 (+0.4% on the day), 4 holdings",
        "Review: http://x/signals",
    ]
    assert whatsapp.digest([DailyReport(market=Market.IN, session=date(2026, 10, 30))], "http://x") is None


def test_template_message_is_sent_to_each_number():
    config, secrets = configured()
    session = Session()
    assert whatsapp.send_message(config, secrets, "line one\nline two", session=session) == 1
    url, body, headers = session.calls[0]
    assert url == "https://graph.facebook.com/v21.0/1234567890/messages"
    assert headers["Authorization"] == "Bearer tok" and body["to"] == "919812345678"
    assert body["type"] == "template" and body["template"]["name"] == "trademare_account_alert"
    assert body["template"]["components"][0]["parameters"][0]["text"] == "line one. line two"
    config, secrets = configured(mode="text")
    whatsapp.send_message(config, secrets, "hi", session=(s := Session()))
    assert s.calls[0][1]["type"] == "text" and s.calls[0][1]["text"]["body"] == "hi"


def test_errors_are_reported_not_raised(monkeypatch):
    config, secrets = configured()
    assert whatsapp.ready(config, Secrets(_env_file=None)) == "WHATSAPP_TOKEN is missing in .env"
    bad = Session(status=400, body={"error": {"message": "Template name does not exist"}})
    monkeypatch.setattr(whatsapp, "requests", bad)
    assert (
        whatsapp.send_text(config, secrets, "x") == "whatsapp failed: HTTP 400: Template name does not exist"
    )


def test_phone_sends_on_every_enabled_channel(monkeypatch):
    config, secrets = configured()
    sent = []
    monkeypatch.setattr(whatsapp, "send_message", lambda c, s, text, session=None: sent.append(text) or 1)
    out = phone.send_text(config, secrets, "TradeMare: test")
    assert out == "whatsapp sent (1)" and sent == ["TradeMare: test"]
