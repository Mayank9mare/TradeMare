"""Phone alerts on every channel that is switched on (WhatsApp and/or Telegram).

Callers use these instead of a specific channel, so turning a channel on in the settings is enough
for every alert (signals, fills, stops, health, IPOs, logins) to reach it. Never raises.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from trademare.config.schema import AppConfig
from trademare.config.secrets import Secrets
from trademare.notify import telegram, whatsapp

log = logging.getLogger(__name__)


def _join(results: list[str]) -> str:
    """Report only channels that are switched on."""
    on = [r for r in results if r and not r.endswith(" off")]
    return "; ".join(on) if on else "no phone channel on"


def send_text(config: AppConfig, secrets: Secrets, text: str) -> str:
    return _join([telegram.send_text(config, secrets, text), whatsapp.send_text(config, secrets, text)])


def ipo_cards(config: AppConfig, today: date):
    """Indian IPO cards (cached NSE data; refreshed when stale). Empty on any problem."""
    try:
        from trademare.analysis.ipo import build_cards
        from trademare.analysis.ipo_study import load_study
        from trademare.data.ipo import IpoService, listing_prices

        service = IpoService(config.data.cache_dir)
        ipos = service.india(today)
        study = load_study(config.data.cache_dir)
        return build_cards(ipos, today, study, listing_prices(service, ipos, today))
    except Exception as e:
        log.warning("IPO data unavailable for the daily message: %s", e)
        return []


def _today_india() -> date:
    from trademare.data.ipo import ist_now

    return ist_now().date()


def whatsapp_daily(config: AppConfig, secrets: Secrets, reports) -> str:
    """The daily table picture; plain sentences if there is no card template or Meta refuses it."""
    from trademare.notify import cards

    wa = config.notifications.whatsapp
    india = any(r.market.value == "IN" for r in reports)
    ipos = None
    if wa.include_ipos and india:
        from trademare.data.ipo import ist_now

        now = ist_now()
        ipos = cards.ipo_table(ipo_cards(config, now.date()), now.date(), after_close=now.hour >= 17)
    if wa.daily_card_template:
        try:
            from trademare.data.store import ParquetStore

            store = ParquetStore(config.data.cache_dir)
            blocks = []
            for r in reports:
                closes = cards.last_closes(store, r.market, [s.symbol for s in r.signals])
                if b := cards.daily_block(r, telegram.ALERT_PREFIXES, telegram.HEADS_UP, closes):
                    blocks.append(b)
            if blocks or ipos:
                png = cards.render_daily(blocks, ipos)
                caption = cards.daily_caption(blocks, ipos, reports)
                n = whatsapp.send_media(
                    config,
                    secrets,
                    wa.daily_card_template,
                    "image",
                    png,
                    "image/png",
                    "trademare-daily.png",
                    [caption],
                )
                return f"whatsapp card sent ({n})"
        except Exception as e:  # template not approved yet, upload failed, drawing failed: send text
            log.warning("whatsapp daily card not sent, sending text instead: %s", e)
    text = whatsapp.digest(reports, config.notifications.dashboard_url)
    if ipos:
        names = ", ".join(f"{row[0]} ({row[1].lower()})" for row in ipos.rows)
        text = (text + "\n" if text else "") + f"IPOs: {names}"
    if text is None and wa.send_empty:
        text = "Nothing new today. " + ", ".join(
            f"{whatsapp.NAMES.get(r.market, r.market.value)}: {r.open_positions} holdings" for r in reports
        )
    return whatsapp.send_text(config, secrets, text) if text else "whatsapp: nothing to send"


def send_alerts(config: AppConfig, secrets: Secrets, reports) -> str:
    """The daily summary: Telegram gets text lines; WhatsApp gets the table picture (or sentences)."""
    results = [telegram.send_alerts(config, secrets, reports)]
    if (missing := whatsapp.ready(config, secrets)) is None:
        results.append(whatsapp_daily(config, secrets, reports))
    else:
        results.append(missing)
    return _join(results)


def send_weekly(config: AppConfig, secrets: Secrets, week_end: date | None = None, send: bool = True):
    """The Saturday PDF for every enabled market with a paper account. Returns (pdf, summary, outcome)."""
    from trademare.data.service import DataService
    from trademare.notify import cards
    from trademare.trading.pipeline import database

    wa = config.notifications.whatsapp
    today = _today_india()
    week_end = week_end or today - timedelta(days=(today.weekday() - 4) % 7)  # last Friday
    service = DataService(config, secrets)
    db = database(config)
    ipos = ipo_cards(config, today) if wa.include_ipos else None
    weeks = [
        cards.weekly_data(config, db, service.store, service.events(), market, week_end, ipos)
        for market in config.enabled_markets()
    ]
    weeks = [w for w in weeks if w.equity is not None or w.ipos] or weeks[:1]
    pdf = cards.render_weekly(weeks)
    summary = "; ".join(f"{cards.NAMES.get(w.market, w.market.value)}: {w.summary()}" for w in weeks)
    if not send:
        return pdf, summary, "not sent"
    if not wa.weekly_template:
        return pdf, summary, "weekly report off (notifications.whatsapp.weekly_template is empty)"
    if (missing := whatsapp.ready(config, secrets)) is not None:
        return pdf, summary, missing
    try:
        n = whatsapp.send_media(
            config,
            secrets,
            wa.weekly_template,
            "document",
            pdf,
            "application/pdf",
            f"TradeMare weekly {week_end:%d %b %Y}.pdf",
            [f"{week_end.day} {week_end:%b %Y}", summary],
        )
        return pdf, summary, f"whatsapp weekly sent ({n})"
    except Exception as e:
        log.warning("weekly report not sent: %s", e)
        return pdf, summary, f"whatsapp weekly failed: {e}"


def send_failure(config: AppConfig, secrets: Secrets, market: str, error: str) -> None:
    telegram.send_failure(config, secrets, market, error)
    whatsapp.send_text(
        config,
        secrets,
        f"🚨 {market} daily run failed: {error[:400]}. Stops at the broker still stand; check the server log",
    )
