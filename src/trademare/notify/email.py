"""Daily email digest: new signals (with dashboard links), fills, account and risk status.

Sent via SMTP when ``notifications.email.enabled`` and SMTP settings exist in
.env; otherwise saved as HTML under data/outbox/ for preview.
"""

from __future__ import annotations

import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from trademare.config.schema import AppConfig
from trademare.config.secrets import Secrets
from trademare.trading.desk import DailyReport
from trademare.web.formatting import money, pct

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(default=True, default_for_string=True),
)
_env.filters.update(money=money, pct=pct)

TEMPLATE = _env.get_template("digest.html")


def render_digest(reports: list[DailyReport], base_url: str) -> tuple[str, str]:
    """(subject, html)."""
    n = sum(len(r.signals) for r in reports)
    markets = "/".join(r.market.value for r in reports)
    kill = any(r.kill_switch for r in reports)
    subject = f"TradeMare {markets}: {n} new signal{'s' if n != 1 else ''}" + (
        " — KILL SWITCH" if kill else ""
    )
    html = TEMPLATE.render(
        reports=reports, base_url=base_url, generated=datetime.now().strftime("%Y-%m-%d %H:%M")
    )
    return subject, html


def send_digest(
    config: AppConfig, secrets: Secrets, reports: list[DailyReport], base_url: str = "http://127.0.0.1:8000"
) -> str:
    """Send (or save) the digest; returns where it went."""
    email = config.notifications.email
    if not reports or (
        email.send_empty_digest is False and not any(r.signals or r.fills or r.kill_switch for r in reports)
    ):
        return "skipped (nothing to report)"
    subject, html = render_digest(reports, base_url)
    if email.enabled and email.to and secrets.smtp_host and secrets.smtp_user and secrets.smtp_password:
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = (
            subject,
            secrets.smtp_from or secrets.smtp_user,
            ", ".join(email.to),
        )
        msg.set_content("Open this email in an HTML-capable client, or visit " + base_url + "/signals")
        msg.add_alternative(html, subtype="html")
        with smtplib.SMTP(secrets.smtp_host, secrets.smtp_port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(secrets.smtp_user, secrets.smtp_password.get_secret_value())
            smtp.send_message(msg)
        return f"emailed to {', '.join(email.to)}"
    outbox = Path(config.data.cache_dir).parent / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    path = outbox / f"{datetime.now():%Y%m%d-%H%M%S}-{'-'.join(r.market.value for r in reports)}.html"
    path.write_text(f"<title>{subject}</title>\n{html}")
    return f"saved to {path} (email not configured)"
