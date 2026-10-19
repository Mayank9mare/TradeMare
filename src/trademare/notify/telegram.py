"""Phone alerts through a Telegram bot (free; no app of our own needed).

Setup: create a bot with @BotFather, put its token in .env as TELEGRAM_BOT_TOKEN, send the bot any
message, then run ``trademare notify telegram-setup`` to find your chat id.
Sending never raises: a failed alert is logged and the daily run carries on.
"""

from __future__ import annotations

import html
import logging

import requests

from trademare.config.schema import AppConfig
from trademare.config.secrets import Secrets
from trademare.trading.desk import DailyReport

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4000  # Telegram's limit is 4096 characters
ALERT_PREFIXES = ("ALERT", "WARNING")
HEADS_UP = "HEADS-UP"


def _call(token: str, method: str, **payload) -> dict:
    r = requests.post(API.format(token=token, method=method), json=payload, timeout=15)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", f"HTTP {r.status_code}"))
    return data["result"]


def send_message(token: str, chat_id: str, text: str) -> None:
    if len(text) > MAX_LEN:
        text = text[: MAX_LEN - 20] + "\n… (truncated)"
    _call(token, "sendMessage", chat_id=chat_id, text=text, parse_mode="HTML", disable_web_page_preview=True)


def find_chat_ids(token: str) -> list[tuple[str, str]]:
    """Chats that recently messaged the bot: [(chat_id, name)]."""
    seen: dict[str, str] = {}
    for update in _call(token, "getUpdates"):
        chat = (update.get("message") or update.get("my_chat_member") or {}).get("chat")
        if chat:
            name = chat.get("username") or chat.get("title") or chat.get("first_name") or ""
            seen[str(chat["id"])] = name
    return list(seen.items())


def format_alerts(reports: list[DailyReport], dashboard_url: str) -> str | None:
    """Short message: urgent alerts first, then new signals. None when there is nothing to say."""
    lines: list[str] = []
    for r in reports:
        alerts = [n for n in r.notes if n.startswith(ALERT_PREFIXES)]
        heads_up = [n.removeprefix(HEADS_UP).strip() for n in r.notes if n.startswith(HEADS_UP)]
        if r.kill_switch:
            alerts.insert(0, f"Kill switch tripped: {r.kill_reason}. New entries are blocked.")
        entries = [s for s in r.signals if s.action == "ENTRY"]
        exits = [s for s in r.signals if s.action == "EXIT"]
        ended = r.orders_ended
        if not (alerts or heads_up or entries or exits or ended or r.fills):
            continue
        lines.append(f"<b>{r.market.value} · {r.session}</b>")
        lines += [f"🚨 {html.escape(a)}" for a in alerts]
        lines += [f"✅ {html.escape(f)}" for f in r.fills]
        lines += [f"⏹ {html.escape(e)}" for e in ended]
        lines += [f"📅 {html.escape(h)}" for h in heads_up]
        for s in entries:
            stop = f", stop {s.stop_price:,.2f}" if s.stop_price is not None else ""
            lines.append(
                f"🟢 {s.direction} {html.escape(s.symbol)} x{s.quantity} @ {s.entry_price:,.2f}{stop}"
            )
            lines += [f"    ⚠️ {html.escape(w)}" for w in (s.reasons or {}).get("events", [])]
        for s in exits:
            lines.append(f"🔴 EXIT {html.escape(s.symbol)} ({html.escape(s.exit_reason or 'signal')})")
            lines += [f"    💰 {html.escape(t)}" for t in (s.reasons or {}).get("tax", [])]
        if r.equity is not None:
            lines.append(f"Equity {r.equity:,.0f} · {r.open_positions} open")
        lines.append("")
    if not lines:
        return None
    pending = sum(len(r.signals) for r in reports)
    if pending:
        plural = "s" if pending != 1 else ""
        lines.append(f'<a href="{html.escape(dashboard_url)}/signals">Review {pending} signal{plural}</a>')
    return "\n".join(lines).strip()


def send_alerts(config: AppConfig, secrets: Secrets, reports: list[DailyReport]) -> str:
    tg = config.notifications.telegram
    if not tg.enabled:
        return "telegram off"
    if not secrets.telegram_bot_token or not tg.chat_id:
        return "telegram enabled but TELEGRAM_BOT_TOKEN or notifications.telegram.chat_id is missing"
    text = format_alerts(reports, config.notifications.dashboard_url)
    if text is None:
        if not tg.send_empty:
            return "telegram: nothing to send"
        text = "No new signals or alerts today. " + ", ".join(
            f"{r.market.value}: {r.open_positions} open" for r in reports
        )
    try:
        send_message(secrets.telegram_bot_token.get_secret_value(), tg.chat_id, text)
    except Exception as e:  # never break the daily run over a notification
        log.warning("telegram send failed: %s", e)
        return f"telegram failed: {e}"
    return "telegram sent"


def send_text(config: AppConfig, secrets: Secrets, text: str) -> str:
    """Plain-text alert (escaped); never raises."""
    tg = config.notifications.telegram
    if not (tg.enabled and tg.chat_id and secrets.telegram_bot_token):
        return "telegram off"
    try:
        send_message(secrets.telegram_bot_token.get_secret_value(), tg.chat_id, html.escape(text))
    except Exception as e:
        log.warning("telegram alert not sent: %s", e)
        return f"telegram failed: {e}"
    return "telegram sent"


def send_failure(config: AppConfig, secrets: Secrets, market: str, error: str) -> None:
    """The daily run itself failed: worth a phone alert, since no digest will arrive."""
    tg = config.notifications.telegram
    if not (tg.enabled and tg.chat_id and secrets.telegram_bot_token):
        return
    try:
        send_message(
            secrets.telegram_bot_token.get_secret_value(),
            tg.chat_id,
            f"🚨 <b>{html.escape(market)} daily run failed</b>\n{html.escape(error[:500])}\n"
            "Stops at the broker still stand; check the server log.",
        )
    except Exception as e:
        log.warning("telegram failure alert not sent: %s", e)
