from datetime import date

import pytest

from tests.conftest import REPO_CONFIG
from trademare.config import Secrets, load_config
from trademare.core.models import Market
from trademare.notify import telegram
from trademare.storage.db import SignalRow
from trademare.trading.desk import DailyReport


def report(**kw) -> DailyReport:
    base = dict(market=Market.IN, session=date(2026, 10, 16), equity=1_002_000.0, open_positions=3)
    return DailyReport(**(base | kw))


def signal(action="ENTRY", symbol="INFY", exit_reason=None) -> SignalRow:
    return SignalRow(
        id=symbol, market="IN", symbol=symbol, direction="LONG", action=action, quantity=12,
        entry_price=1500.0, stop_price=1400.0, score=0.9, confidence=1.0, reasons={}, exit_reason=exit_reason,
    )  # fmt: skip


@pytest.fixture
def config():
    c = load_config(REPO_CONFIG).model_copy(deep=True)
    c.notifications.telegram.enabled, c.notifications.telegram.chat_id = True, "42"
    return c


@pytest.fixture
def sent(monkeypatch):
    calls = []

    class Resp:
        status_code = 200

        def json(self):
            return {"ok": True, "result": {}}

    def post(url, json, timeout):
        calls.append((url, json))
        return Resp()

    monkeypatch.setattr(telegram.requests, "post", post)
    return calls


def secrets(token="T0K"):
    return Secrets(_env_file=None, telegram_bot_token=token)


def test_message_puts_alerts_first_then_signals():
    r = report(
        signals=[signal(), signal("EXIT", "TCS", "stop (gapped past limit)")],
        notes=[
            "INFY: protective stop re-placed at 1,400.00",
            "ALERT TCS: stop (gapped past limit) — market exit placed",
        ],
    )
    text = telegram.format_alerts([r], "http://box:8000")
    lines = text.splitlines()
    assert lines[0] == "<b>IN · 2026-10-16</b>" and lines[1].startswith("🚨 ALERT TCS")
    assert "re-placed" not in text  # routine notes stay out of phone alerts
    assert "🟢 LONG INFY x12 @ 1,500.00, stop 1,400.00" in text
    assert "🔴 EXIT TCS (stop (gapped past limit))" in text
    assert 'href="http://box:8000/signals">Review 2 signals' in text


def test_kill_switch_is_an_alert_and_quiet_days_send_nothing():
    assert telegram.format_alerts([report()], "u") is None
    text = telegram.format_alerts([report(kill_switch=True, kill_reason="drawdown -10.2%")], "u")
    assert "Kill switch tripped: drawdown -10.2%" in text


def test_send_alerts(config, sent):
    assert telegram.send_alerts(config, secrets(), [report()]) == "telegram: nothing to send"
    assert telegram.send_alerts(config, secrets(), [report(signals=[signal()])]) == "telegram sent"
    url, body = sent[-1]
    assert url == "https://api.telegram.org/botT0K/sendMessage" and body["chat_id"] == "42"
    assert body["parse_mode"] == "HTML"


def test_send_alerts_never_raises(config, monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("no network")

    monkeypatch.setattr(telegram.requests, "post", boom)
    result = telegram.send_alerts(config, secrets(), [report(signals=[signal()])])
    assert result.startswith("telegram failed")
    telegram.send_failure(config, secrets(), "IN", "boom")  # also swallowed


def test_disabled_or_unconfigured(config, sent):
    assert "missing" in telegram.send_alerts(config, Secrets(_env_file=None), [report(signals=[signal()])])
    config.notifications.telegram.enabled = False
    assert telegram.send_alerts(config, secrets(), [report(signals=[signal()])]) == "telegram off"
    assert sent == []
