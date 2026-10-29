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
    text = "<b>IN · 2026-10-29</b>\n🟢 LONG HSCL x14 @ 536.25\n\n  📅 results   soon &amp; more"
    assert whatsapp.flatten(text) == "IN · 2026-10-29 · 🟢 LONG HSCL x14 @ 536.25 · 📅 results soon & more"
    long = "\n".join(f"line {i} " + "x" * 90 for i in range(30))
    parts = whatsapp.chunks(long)
    assert len(parts) > 1 and all(len(p) <= whatsapp.MAX_PARAM for p in parts)
    assert " · ".join(parts).count("line ") == 30  # nothing lost
    assert "\n" not in "".join(parts)


def test_template_message_is_sent_to_each_number():
    config, secrets = configured()
    session = Session()
    assert whatsapp.send_message(config, secrets, "line one\nline two", session=session) == 1
    url, body, headers = session.calls[0]
    assert url == "https://graph.facebook.com/v21.0/1234567890/messages"
    assert headers["Authorization"] == "Bearer tok" and body["to"] == "919812345678"
    assert body["type"] == "template" and body["template"]["name"] == "trademare_alert"
    assert body["template"]["components"][0]["parameters"][0]["text"] == "line one · line two"
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
