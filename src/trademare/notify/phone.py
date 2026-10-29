"""Phone alerts on every channel that is switched on (WhatsApp and/or Telegram).

Callers use these instead of a specific channel, so turning a channel on in the settings is enough
for every alert (signals, fills, stops, health, IPOs, logins) to reach it. Never raises.
"""

from __future__ import annotations

from trademare.config.schema import AppConfig
from trademare.config.secrets import Secrets
from trademare.notify import telegram, whatsapp


def _join(results: list[str]) -> str:
    """Report only channels that are switched on."""
    on = [r for r in results if r and not r.endswith(" off")]
    return "; ".join(on) if on else "no phone channel on"


def send_text(config: AppConfig, secrets: Secrets, text: str) -> str:
    return _join([telegram.send_text(config, secrets, text), whatsapp.send_text(config, secrets, text)])


def send_alerts(config: AppConfig, secrets: Secrets, reports) -> str:
    """The daily summary: urgent alerts, then new signals (same content on both channels)."""
    results = [telegram.send_alerts(config, secrets, reports)]
    if whatsapp.ready(config, secrets) is None:
        text = telegram.format_alerts(reports, config.notifications.dashboard_url)
        if text is None and config.notifications.whatsapp.send_empty:
            text = "No new signals or alerts today. " + ", ".join(
                f"{r.market.value}: {r.open_positions} open" for r in reports
            )
        results.append(whatsapp.send_text(config, secrets, text) if text else "whatsapp: nothing to send")
    else:
        results.append(whatsapp.ready(config, secrets) or "")
    return _join(results)


def send_failure(config: AppConfig, secrets: Secrets, market: str, error: str) -> None:
    telegram.send_failure(config, secrets, market, error)
    whatsapp.send_text(
        config,
        secrets,
        f"🚨 {market} daily run failed: {error[:400]}. Stops at the broker still stand; check the server log",
    )
