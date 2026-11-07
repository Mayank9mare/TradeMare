# ruff: noqa: RUF002  (times and dash signs in user-facing text)
"""IPO page: Indian mainboard issues (open, upcoming, recently closed and listed) and the US calendar."""

from __future__ import annotations

import math
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from trademare.analysis.explain import SignalExplainer, llm_ready
from trademare.analysis.ipo import KIND_TEXT, build_cards, ipo_facts
from trademare.analysis.ipo_study import BUCKETS, load_study
from trademare.analysis.llm import LLMError
from trademare.config.schema import AppConfig
from trademare.config.secrets import Secrets
from trademare.core.models import Market
from trademare.data.ipo import IpoService, ist_now, listing_prices
from trademare.storage.db import Database, EventRow

ORDER = {"open": 0, "upcoming": 1, "closed": 2, "listed": 3}


def times_width(x: float | None) -> float:
    """Bar width (%) for a subscription figure on a log scale: 1× ≈ 12%, 10× ≈ 42%, 300× and up full."""
    if not x or x <= 0:
        return 0.0
    return round(min(100.0, math.log10(x + 1) / math.log10(301) * 100), 1)


def register_ipo_routes(
    app: FastAPI, templates: Jinja2Templates, config: AppConfig, db: Database, secrets: Secrets
) -> None:
    service = IpoService(config.data.cache_dir)
    explainer: list = []
    templates.env.filters["times_width"] = times_width

    def india_cards():
        today = ist_now().date()
        ipos = service.india(today)
        study = load_study(config.data.cache_dir)
        cards = build_cards(ipos, today, study, listing_prices(service, ipos, today))
        cards.sort(key=lambda c: (ORDER[c.status], c.ipo.end or today))
        return today, cards, study

    @app.get("/ipos", response_class=HTMLResponse)
    def ipos_page(request: Request, msg: str | None = None, err: str | None = None):
        today, cards, study = india_cards()
        us = []
        if Market.US in config.markets:
            us = sorted(service.us(today), key=lambda i: i.start or today, reverse=True)
        asks: dict[str, list[dict]] = {}
        with db.session() as s:
            for ev in s.scalars(select(EventRow).where(EventRow.kind == "ipo_ask").order_by(EventRow.ts)):
                asks.setdefault(ev.data.get("symbol"), []).append(ev.data | {"ts": ev.ts})
        return templates.TemplateResponse(
            request,
            "ipos.html",
            {
                "today": today,
                "cards": cards,
                "study": study,
                "buckets": [b[0] for b in BUCKETS],
                "kind_text": KIND_TEXT,
                "us": us,
                "asks": asks,
                "llm_missing": llm_ready(config, secrets),
                "msg": msg,
                "err": err,
            },
        )

    @app.post("/ipos/{symbol}/ask")
    def ask_ipo(symbol: str, csrf_token: str = Form(...), question: str = Form("")):
        import secrets as pysecrets

        if not pysecrets.compare_digest(csrf_token, templates.env.globals["csrf_token"]):
            raise HTTPException(403, "invalid form token; reload the page and try again")
        anchor = f"#ipo-{quote(symbol)}"
        if missing := llm_ready(config, secrets):
            return RedirectResponse(f"/ipos?err={quote(missing)}{anchor}", status_code=303)
        _, cards, _ = india_cards()
        card = next((c for c in cards if c.symbol == symbol), None)
        if card is None:
            raise HTTPException(404, "no such IPO")
        if not explainer:
            from trademare.analysis.llm import create_llm_client

            ledger = Path(config.data.cache_dir).parent / "sentiment"
            explainer.append(SignalExplainer(config, create_llm_client(config.llm, secrets), ledger))
        try:
            answer = explainer[0].ask_ipo(ipo_facts(card), question)
        except LLMError as e:
            return RedirectResponse(f"/ipos?err={quote(f'Claude: {e}')}{anchor}", status_code=303)
        with db.session() as s:
            db.log(
                s, "ipo_ask", f"asked about IPO {symbol}: {answer.question}", Market.IN.value, symbol=symbol,
                question=answer.question, answer=answer.answer, risks=answer.risks, cost_usd=answer.cost_usd,
            )  # fmt: skip
        return RedirectResponse(f"/ipos{anchor}", status_code=303)
