"""Phone alerts on WhatsApp through Meta's official WhatsApp Cloud API.

A business number (Meta gives a free test number) messages the owner's own WhatsApp. Messages a
business starts must use a template Meta has approved, so alerts are sent as the template
``trademare_alert`` whose body is ``TradeMare update: {{1}}. Open your dashboard for details.``
Template parameters cannot contain line breaks, so each alert is flattened onto one line and long
alerts are split into several messages. Each message costs about ₹0.12 + GST in India (utility
rate, Oct 2026). Sending never raises: a failed alert is logged and the caller carries on.

Setup: see docs/whatsapp-setup.md.
"""

from __future__ import annotations

import html
import logging
import re

import requests

from trademare.config.schema import AppConfig
from trademare.config.secrets import Secrets

log = logging.getLogger(__name__)

API = "https://graph.facebook.com/{version}/{phone_number_id}/messages"
MAX_PARAM = 1000  # Meta allows 1,024 characters per template parameter


def flatten(text: str) -> str:
    """One line, no HTML, no runs of spaces: what a template parameter accepts."""
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    parts = [line.strip() for line in text.splitlines() if line.strip()]
    flat = " · ".join(parts)
    return re.sub(r"\s{2,}", " ", flat).strip()


def chunks(text: str, size: int = MAX_PARAM) -> list[str]:
    """Split a flattened alert at separators so each piece fits one template parameter."""
    flat = flatten(text)
    if len(flat) <= size:
        return [flat] if flat else []
    out, current = [], ""
    for part in flat.split(" · "):
        piece = part if len(part) <= size else part[: size - 1] + "…"
        candidate = f"{current} · {piece}" if current else piece
        if len(candidate) > size:
            out.append(current)
            current = piece
        else:
            current = candidate
    if current:
        out.append(current)
    return out


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
