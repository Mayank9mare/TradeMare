"""Dashboard routes for signals (approve/reject/edit) and paper/live portfolios."""

from __future__ import annotations

import secrets as pysecrets
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from trademare.analysis.forensic import PIOTROSKI_LABELS
from trademare.config.schema import AppConfig
from trademare.config.secrets import Secrets
from trademare.core.models import Direction, Market
from trademare.data.service import DataService
from trademare.storage.db import Database, EquityRow, EventRow, OrderRow, PositionRow, SignalRow
from trademare.strategy.evidence import FUND_METRICS, TECH_METRICS
from trademare.trading.desk import DeskError, TradingDesk
from trademare.trading.journal import build_journal
from trademare.trading.tracking import MIN_SESSIONS, Tracking, compare_with_backtest
from trademare.web.overview_data import sparkline


def register_trading_routes(
    app: FastAPI,
    templates: Jinja2Templates,
    config: AppConfig,
    service: DataService,
    db: Database,
    secrets: Secrets,
) -> None:
    csrf = pysecrets.token_urlsafe(32)
    templates.env.globals["csrf_token"] = csrf
    desks: dict[Market, TradingDesk] = {}

    def desk(market: Market) -> TradingDesk:
        if market not in desks:
            desks[market] = TradingDesk(service, db, market, secrets)
        return desks[market]

    def check_csrf(token: str) -> None:
        if not pysecrets.compare_digest(token, csrf):
            raise HTTPException(403, "invalid form token; reload the page and try again")

    def back(path: str, msg: str | None = None, err: str | None = None) -> RedirectResponse:
        query = f"?msg={quote(msg)}" if msg else f"?err={quote(err)}" if err else ""
        return RedirectResponse(path + query, status_code=303)

    def market_of(code: str) -> Market:
        try:
            market = Market(code.upper())
        except ValueError:
            raise HTTPException(404, f"unknown market {code}") from None
        if market not in config.markets:
            raise HTTPException(404, f"market {market} not configured")
        return market

    @app.get("/signals", response_class=HTMLResponse)
    def signals_page(request: Request, msg: str | None = None, err: str | None = None):
        now = datetime.now(UTC)
        with db.session() as s:
            pending_rows = s.scalars(select(SignalRow).where(SignalRow.status == "PENDING"))
            needs = {
                Market(sig.market)
                for sig in pending_rows
                if not sig.evidence
                or (config.events.enabled and sig.action == "ENTRY" and "events" not in sig.evidence)
                or (sig.evidence.get("fundamental") and "forensic" not in sig.evidence["fundamental"])
            }
        for market in needs:  # signals created before evidence was recorded
            desk(market).ensure_evidence()
        with db.session() as s:
            pending = list(
                s.scalars(
                    select(SignalRow)
                    .where(SignalRow.status == "PENDING")
                    .order_by(SignalRow.market, SignalRow.action.desc(), SignalRow.score.desc())
                )
            )
            recent = list(
                s.scalars(
                    select(SignalRow)
                    .where(SignalRow.status != "PENDING")
                    .order_by(SignalRow.created_at.desc())
                    .limit(40)
                )
            )
        for sig in pending:
            exp = (
                sig.expires_at.replace(tzinfo=UTC)
                if sig.expires_at and not sig.expires_at.tzinfo
                else sig.expires_at
            )
            sig.is_expired = bool(exp and exp < now)  # type: ignore[attr-defined]
        charts = {}
        for sig in pending:  # 6-month price line up to the scan date
            bars = service.store.read_bars(Market(sig.market), sig.symbol)
            if bars is not None and len(bars):
                charts[sig.id] = sparkline(bars["close"].loc[: str(sig.scan_date)].iloc[-126:])
        return templates.TemplateResponse(
            request,
            "signals.html",
            {
                "pending": pending,
                "recent": recent,
                "msg": msg,
                "err": err,
                "config": config,
                "charts": charts,
                "tech_metrics": TECH_METRICS,
                "fund_metrics": FUND_METRICS,
                "piotroski_labels": PIOTROSKI_LABELS,
            },
        )

    @app.post("/signals/{signal_id}/approve")
    def approve(
        signal_id: str,
        csrf_token: str = Form(...),
        quantity: int | None = Form(None),
        limit: float | None = Form(None),
        stop: float | None = Form(None),
    ):
        check_csrf(csrf_token)
        with db.session() as s:
            sig = s.get(SignalRow, signal_id)
            if sig is None:
                raise HTTPException(404, "no such signal")
            market = Market(sig.market)
        try:
            order = desk(market).approve(signal_id, quantity=quantity, limit=limit, stop=stop)
        except DeskError as e:
            return back("/signals", err=str(e))
        price = f" @ {order.limit_price:,.2f}" if order.limit_price else " at the open"
        return back(
            "/signals",
            msg=f"Approved: {order.side} {order.quantity} {order.symbol}{price} on {order.valid_until}",
        )

    @app.post("/signals/{signal_id}/reject")
    def reject(signal_id: str, csrf_token: str = Form(...)):
        check_csrf(csrf_token)
        with db.session() as s:
            sig = s.get(SignalRow, signal_id)
            if sig is None:
                raise HTTPException(404, "no such signal")
            market, symbol = Market(sig.market), sig.symbol
        try:
            desk(market).reject(signal_id)
        except DeskError as e:
            return back("/signals", err=str(e))
        return back("/signals", msg=f"Rejected {symbol}")

    @app.get("/portfolio/{code}", response_class=HTMLResponse)
    def portfolio_page(request: Request, code: str, msg: str | None = None, err: str | None = None):
        market = market_of(code)
        d = desk(market)
        with db.session() as s:
            state = d.account_state(s)
            equity, cash, gross = d.equity(s)
            positions = d.db.open_positions(s, market.value)
            for p in positions:
                sign = 1 if p.direction == Direction.LONG.value else -1
                px = p.last_price or p.entry_price
                p.unrealized = sign * (px - p.entry_price) * p.quantity - p.entry_costs  # type: ignore[attr-defined]
                p.unrealized_pct = sign * (px / p.entry_price - 1)  # type: ignore[attr-defined]
            orders = list(
                s.scalars(
                    select(OrderRow)
                    .where(OrderRow.market == market.value, OrderRow.status == "OPEN")
                    .order_by(OrderRow.created_at.desc())
                )
            )
            closed = list(
                s.scalars(
                    select(PositionRow)
                    .where(PositionRow.market == market.value, PositionRow.status == "closed")
                    .order_by(PositionRow.exit_date.desc())
                    .limit(50)
                )
            )
            events = list(
                s.scalars(
                    select(EventRow)
                    .where(EventRow.market == market.value)
                    .order_by(EventRow.ts.desc())
                    .limit(30)
                )
            )
            realized = sum(p.realized_pnl or 0 for p in closed)
        cfg = config.markets[market]
        return templates.TemplateResponse(
            request,
            "portfolio.html",
            {
                "market": market,
                "cfg": cfg,
                "state": state,
                "equity": equity,
                "cash": cash,
                "gross": gross,
                "positions": positions,
                "orders": orders,
                "closed": closed,
                "events": events,
                "realized": realized,
                "drawdown": equity / state.peak_equity - 1 if state.peak_equity else 0.0,
                "simulated": d.broker.simulated,
                "msg": msg,
                "err": err,
            },
        )

    @app.get("/journal", response_class=HTMLResponse)
    def journal_page(request: Request):
        entries, summary = build_journal(db, service.store)
        return templates.TemplateResponse(request, "journal.html", {"entries": entries, "summary": summary})

    tracking_cache: dict[tuple[Market, object, int], Tracking] = {}

    @app.get("/portfolio/{code}/tracking", response_class=HTMLResponse)
    def tracking_page(request: Request, code: str):
        market = market_of(code)
        with db.session() as s:
            days = list(s.scalars(select(EquityRow.day).where(EquityRow.market == market.value)))
        key = (market, max(days) if days else None, len(days))
        if key not in tracking_cache:  # the backtest replay is only redone after a new trading day
            tracking_cache.clear()
            tracking_cache[key] = compare_with_backtest(service, db, market)
        t = tracking_cache[key]
        lines = None
        if t.curve is not None and len(t.curve) > 1:
            lo, hi = float(t.curve.min().min()), float(t.curve.max().max())
            span = (hi - lo) or 1.0
            n = len(t.curve) - 1

            def path(col: str) -> str:
                pts = [
                    (6 + 588 * i / n, 6 + 148 * (1 - (float(v) - lo) / span))
                    for i, v in enumerate(t.curve[col])
                ]
                return "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts)

            lines = {"paper": path("paper"), "backtest": path("backtest"), "low": lo, "high": hi}
        return templates.TemplateResponse(
            request,
            "tracking.html",
            {
                "market": market,
                "t": t,
                "lines": lines,
                "min_sessions": MIN_SESSIONS,
                "simulated": desk(market).broker.simulated,
            },
        )

    @app.post("/portfolio/{code}/rearm")
    def rearm(code: str, csrf_token: str = Form(...)):
        check_csrf(csrf_token)
        market = market_of(code)
        desk(market).rearm()
        return back(
            f"/portfolio/{market.value}", msg="Kill switch re-armed; peak equity reset to current equity"
        )

    @app.get("/api/portfolio/{code}/equity")
    def equity_api(code: str):
        market = market_of(code)
        with db.session() as s:
            rows = list(
                s.scalars(select(EquityRow).where(EquityRow.market == market.value).order_by(EquityRow.day))
            )
        return JSONResponse({"equity": [{"time": r.day.isoformat(), "value": r.equity} for r in rows]})
