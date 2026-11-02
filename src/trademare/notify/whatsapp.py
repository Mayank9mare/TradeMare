"""Phone alerts on WhatsApp through Meta's official WhatsApp Cloud API.

A business number (Meta gives a free test number) messages the owner's own WhatsApp. Messages a
business starts must use a template Meta has approved, so alerts are sent as the template
``trademare_account_alert`` whose body is ``Alert about your TradeMare trading account: {{1}}.
This is an automatic notice about your orders and positions.``
Template parameters cannot contain line breaks, so each alert is flattened onto one line and long
alerts are split into several messages. Each message costs about ₹0.12 + GST in India (utility
rate, Oct 2026). Sending never raises: a failed alert is logged and the caller carries on.

Setup: see docs/whatsapp-setup.md.
"""

from __future__ import annotations

import html
import logging
import re
from zoneinfo import ZoneInfo

import requests

from trademare.config.schema import AppConfig
from trademare.config.secrets import Secrets
from trademare.core.models import Market
from trademare.web.formatting import indian, money

log = logging.getLogger(__name__)

API = "https://graph.facebook.com/{version}/{phone_number_id}/messages"
MAX_PARAM = 1000  # Meta allows 1,024 characters per template parameter


def _sentences(text: str) -> list[str]:
    """Each non-empty line as a sentence: no HTML, single spaces, ending in punctuation."""
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    out = []
    for line in text.splitlines():
        line = re.sub(r"\s{2,}", " ", line).strip()
        if line:
            out.append(line if line[-1] in ".!?:" else line + ".")
    return out


def flatten(text: str) -> str:
    """One line of plain sentences: what a template parameter accepts (no line breaks)."""
    return " ".join(_sentences(text))


def chunks(text: str, size: int = MAX_PARAM) -> list[str]:
    """Split an alert between sentences so each piece fits one template parameter.

    The template adds its own full stop after the parameter, so a piece's last one is dropped.
    """
    out, current = [], ""
    for sentence in _sentences(text):
        piece = sentence if len(sentence) <= size else sentence[: size - 1] + "…"
        candidate = f"{current} {piece}" if current else piece
        if len(candidate) > size:
            out.append(current)
            current = piece
        else:
            current = candidate
    if current:
        out.append(current)
    return [p[:-1] if p.endswith(".") else p for p in out]


NAMES = {Market.IN: "India", Market.US: "US"}
ZONES = {Market.IN: "Asia/Kolkata", Market.US: "America/New_York"}
EXIT_WHY = {
    "signal": "fell out of the top ranks",
    "regime": "market turned weak",
    "time": "held too long",
    "exit": "its setup says sell",
}
MAX_URGENT = 3


def _rupees(value: float | None, market: Market) -> str:
    return money(value, market, compact=False)


def _whole(value: float, market: Market) -> str:
    """Totals and P&L without paise or cents: ₹2,738."""
    sign = "-" if value < 0 else ""
    return sign + ("₹" + indian(abs(value), 0) if market is Market.IN else f"${abs(value):,.0f}")


def _fill(text: str, market: Market) -> str:
    """'HSCL: LONG 13 @ 509.20 (broker)' -> 'bought 13 HSCL at ₹509.20'."""
    text = text.removesuffix(" (broker)")
    if m := re.fullmatch(r"(\S+): (LONG|SHORT) (\d+) @ ([\d,.]+)", text):
        verb = "bought" if m[2] == "LONG" else "sold short"
        return f"{verb} {m[3]} {m[1]} at {_rupees(float(m[4].replace(',', '')), market)}"
    if m := re.fullmatch(r"(\S+): closed \(([^)]*)\) @ ([\d,.]+), P&L (-?[\d,.]+)", text):
        pnl = float(m[4].replace(",", ""))
        result = f"profit {_whole(pnl, market)}" if pnl >= 0 else f"loss {_whole(-pnl, market)}"
        return f"sold {m[1]} ({m[2]}) at {_rupees(float(m[3].replace(',', '')), market)}, {result}"
    return text


def _unfilled(text: str) -> str:
    """describe()'s 'Buy 6 GESHIP expired on Mon 02 Nov without filling: why.' -> 'GESHIP (why)'."""
    if m := re.fullmatch(r"(?:Buy|Sell) \d+ (\S+) expired on .*? without filling(?:: (.*?))?\.?", text):
        why = m[2] or ""
        if "stayed above your limit" in why:
            why = "price never came down to your limit"
        elif "stayed below your limit" in why:
            why = "price never rose to your limit"
        elif "stayed below your trigger" in why:
            why = "price never rose to the buy-above level"
        return f"{m[1]} ({why})" if why else m[1]
    return text.rstrip(".")


def _deadline(signals, market: Market) -> str:
    ends = [s.expires_at for s in signals if s.expires_at is not None]
    if not ends:
        return ""
    end = min(ends)
    if end.tzinfo is None:
        end = end.replace(tzinfo=ZoneInfo("UTC"))
    local = end.astimezone(ZoneInfo(ZONES[market]))
    return f" by {local:%a} {local.day} {local:%b}, {local:%-I:%M %p}".replace("AM", "am").replace("PM", "pm")


def _setup_tag(sig) -> str:
    strategy = sig.strategy or ""
    return f" ({strategy.removeprefix('setup:').upper()} setup)" if strategy.startswith("setup:") else ""


def _market_digest(r, alert_prefixes: tuple[str, ...], heads_up: str) -> list[str]:
    """Plain sentences for one market's daily run; empty when nothing happened."""
    m = r.market
    urgent = [n.split(" ", 1)[1] if " " in n else n for n in r.notes if n.startswith(alert_prefixes)]
    if r.kill_switch:
        urgent.insert(0, f"kill switch tripped ({r.kill_reason}), new buys are blocked")
    results = [n.removeprefix(heads_up).strip().split(" ", 1)[0] for n in r.notes if n.startswith(heads_up)]
    entries = [s for s in r.signals if s.action == "ENTRY"]
    exits = [s for s in r.signals if s.action == "EXIT"]
    if not (urgent or results or entries or exits or r.orders_ended or r.fills):
        return []
    out = [f"{NAMES.get(m, m.value)}, after {r.session:%a} {r.session.day} {r.session:%b}"]
    for u in urgent[:MAX_URGENT]:
        out.append(f"🚨 {u}")
    if len(urgent) > MAX_URGENT:
        out.append(f"🚨 {len(urgent) - MAX_URGENT} more urgent items on the dashboard")
    if r.fills:
        out.append("Done: " + "; ".join(_fill(f, m) for f in r.fills))
    if r.orders_ended:
        out.append("Not filled: " + "; ".join(_unfilled(e) for e in r.orders_ended))
    if results:
        out.append("Results next session for your holding " + ", ".join(results) + " (big moves possible)")
    if entries:
        names = []
        for s in entries:
            soon = any(w.startswith("Results due") for w in (s.reasons or {}).get("events", []))
            names.append(s.symbol + _setup_tag(s) + (" (results soon)" if soon else ""))
        cost = sum(s.entry_price * s.quantity for s in entries)
        risk = sum(
            abs(s.entry_price - s.stop_price) * s.quantity for s in entries if s.stop_price is not None
        )
        n = len(entries)
        out.append(
            f"{n} new buy idea{'s' if n != 1 else ''} to approve or skip{_deadline(entries, m)}: "
            + ", ".join(names)
            + f". Together {_whole(cost, m)}, at most {_whole(risk, m)} at risk"
        )
    if exits:
        why = [f"{s.symbol} ({EXIT_WHY.get(s.exit_reason or '', s.exit_reason or 'rule')})" for s in exits]
        out.append(f"Sell suggested{_deadline(exits, m)}: " + ", ".join(why))
    if r.equity is not None:
        change = ""
        if r.previous_equity:
            pct = (r.equity / r.previous_equity - 1) * 100
            change = f" ({pct:+.1f}% on the day)"
        out.append(f"Paper portfolio {_whole(r.equity, m)}{change}, {r.open_positions} holdings")
    return out


def digest(reports, dashboard_url: str) -> str | None:
    """The daily summary in short plain sentences (one per line; sent flattened). None if quiet."""
    from trademare.notify.telegram import ALERT_PREFIXES, HEADS_UP

    lines: list[str] = []
    for r in reports:
        lines += _market_digest(r, ALERT_PREFIXES, HEADS_UP)
    if not lines:
        return None
    pending = sum(len(r.signals) for r in reports)
    lines.append(f"Review: {dashboard_url}/signals" if pending else f"Dashboard: {dashboard_url}")
    return "\n".join(lines)


def payload(cfg, to: str, text: str) -> dict:
    if cfg.mode == "text":  # only delivered inside 24 hours of the owner's last message
        return {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": cfg.template,
            "language": {"code": cfg.language},
            "components": [{"type": "body", "parameters": [{"type": "text", "text": text}]}],
        },
    }


def upload_media(
    config: AppConfig, secrets: Secrets, data: bytes, mime: str, filename: str, session=None
) -> str:
    """Upload a picture or PDF to Meta; returns the media id to put in a message (kept 30 days)."""
    wa = config.notifications.whatsapp
    url = API.format(version=wa.api_version, phone_number_id=wa.phone_number_id).replace(
        "/messages", "/media"
    )
    headers = {"Authorization": f"Bearer {secrets.whatsapp_token.get_secret_value()}"}
    r = (session or requests).post(
        url,
        headers=headers,
        data={"messaging_product": "whatsapp", "type": mime},
        files={"file": (filename, data, mime)},
        timeout=60,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"media upload HTTP {r.status_code}: {r.text[:200]}")
    return r.json()["id"]


def media_payload(
    cfg, to: str, template: str, kind: str, media_id: str, params: list[str], filename=None
) -> dict:
    """A template message whose header is an image or a document and whose body takes ``params``."""
    media = {"id": media_id, **({"filename": filename} if filename and kind == "document" else {})}
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": cfg.language},
            "components": [
                {"type": "header", "parameters": [{"type": kind, kind: media}]},
                {"type": "body", "parameters": [{"type": "text", "text": chunks(p)[0]} for p in params]},
            ],
        },
    }


def send_media(
    config: AppConfig,
    secrets: Secrets,
    template: str,
    kind: str,
    data: bytes,
    mime: str,
    filename: str,
    params: list[str],
    session=None,
) -> int:
    """Upload ``data`` and send it with ``template`` to every recipient. Raises on errors."""
    wa = config.notifications.whatsapp
    http = session or requests
    media_id = upload_media(config, secrets, data, mime, filename, session=http)
    url = API.format(version=wa.api_version, phone_number_id=wa.phone_number_id)
    headers = {"Authorization": f"Bearer {secrets.whatsapp_token.get_secret_value()}"}
    sent = 0
    for to in wa.to:
        body = media_payload(wa, to.lstrip("+"), template, kind, media_id, params, filename)
        r = http.post(url, json=body, headers=headers, timeout=30)
        if r.status_code >= 400:
            try:
                detail = r.json().get("error", {}).get("message", r.text[:200])
            except ValueError:
                detail = r.text[:200]
            raise RuntimeError(f"HTTP {r.status_code}: {detail}")
        sent += 1
    return sent


def ready(config: AppConfig, secrets: Secrets) -> str | None:
    """None when WhatsApp can send, else what is missing."""
    wa = config.notifications.whatsapp
    if not wa.enabled:
        return "whatsapp off"
    if not secrets.whatsapp_token:
        return "WHATSAPP_TOKEN is missing in .env"
    if not wa.phone_number_id or not wa.to:
        return "notifications.whatsapp.phone_number_id and .to must be set"
    return None


def send_message(config: AppConfig, secrets: Secrets, text: str, session=None) -> int:
    """Send one alert (split if long) to every recipient; returns messages sent. Raises on errors."""
    wa = config.notifications.whatsapp
    url = API.format(version=wa.api_version, phone_number_id=wa.phone_number_id)
    headers = {"Authorization": f"Bearer {secrets.whatsapp_token.get_secret_value()}"}
    http = session or requests
    sent = 0
    for to in wa.to:
        for part in chunks(text):
            r = http.post(url, json=payload(wa, to.lstrip("+"), part), headers=headers, timeout=20)
            if r.status_code >= 400:
                try:
                    detail = r.json().get("error", {}).get("message", r.text[:200])
                except ValueError:
                    detail = r.text[:200]
                raise RuntimeError(f"HTTP {r.status_code}: {detail}")
            sent += 1
    return sent


def send_text(config: AppConfig, secrets: Secrets, text: str) -> str:
    """Plain alert; never raises."""
    if (missing := ready(config, secrets)) is not None:
        return missing
    try:
        n = send_message(config, secrets, text)
    except Exception as e:
        log.warning("whatsapp alert not sent: %s", e)
        return f"whatsapp failed: {e}"
    return f"whatsapp sent ({n})"
