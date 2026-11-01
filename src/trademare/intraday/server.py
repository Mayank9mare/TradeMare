"""The intraday module's own dashboard and scheduler (``trademare-intraday serve``, port 8766).

Runs independently of ``trademare serve``: a paper poll every 5 minutes on NSE trading days from
09:20 to 15:35 IST, and a page to choose strategies and follow the day's paper trades.
"""

from __future__ import annotations

import logging
import secrets as pysecrets
import threading
from datetime import datetime, time
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from trademare.intraday import config as cfgmod
from trademare.intraday import paper
from trademare.intraday import study as study_mod
from trademare.intraday.strategies import STRATEGIES

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
HERE = Path(__file__).parent
FIRST, LAST = time(9, 20), time(15, 35)


def kite_fetcher(cfg):
    from trademare.intraday.kite_data import KiteCandles, kite_client

    candles = KiteCandles(kite_client(), cfg.root)
    return lambda symbol, start, end: candles.fetch(symbol, start, end, cfg.interval)


def scheduled_poll(config_path: str | None = None, now: datetime | None = None, fetcher=None) -> str:
    """One scheduled poll; returns what happened (also logged in the module's database)."""
    from trademare.core.clock import MarketClock
    from trademare.core.models import Market

    now = now or datetime.now(IST).replace(tzinfo=None)
    if not MarketClock(Market.IN).is_session(now.date()) or not (FIRST <= now.time() <= LAST):
        return "market closed"
    cfg = cfgmod.load(config_path)
    try:
        fetch = fetcher or kite_fetcher(cfg)
    except Exception as e:
        paper.log_poll(cfg, False, str(e))
        return str(e)
    result = paper.poll(cfg, fetch, now)
    return f"{len(result['rows'])} trades"


def create_app(config_path: str | None = None, start_scheduler: bool = False) -> FastAPI:
    app = FastAPI(title="TradeMare intraday")
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    token = pysecrets.token_urlsafe(16)
    busy = threading.Lock()

    if start_scheduler:
        from apscheduler.schedulers.background import BackgroundScheduler

        sched = BackgroundScheduler(timezone=IST)
        sched.add_job(
            scheduled_poll, "cron", day_of_week="mon-fri", hour="9-15", minute="*/5", second=20,
            args=[config_path], id="intraday-poll", max_instances=1, coalesce=True,
        )  # fmt: skip
        sched.start()
        app.state.scheduler = sched

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, msg: str | None = None, err: str | None = None):
        from trademare.execution.brokers.zerodha import session_active

        cfg = cfgmod.load(config_path)
        record = study_mod.load(cfg)
        run, held_back = paper.allowed_strategies(cfg, record)
        today = paper.load_today(cfg)
        now = datetime.now(IST)
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "cfg": cfg,
                "strategies": STRATEGIES,
                "record": record,
                "periods": study_mod.PERIODS,
                "running": run,
                "held_back": held_back,
                "today": today if today and today.get("day") == str(now.date()) else None,
                "history": paper.history(cfg),
                "polls": paper.last_polls(cfg),
                "kite_ok": session_active(),
                "now": now,
                "token": token,
                "msg": msg,
                "err": err,
            },
        )

    @app.post("/strategies")
    async def save(request: Request):
        form = await request.form()
        if not pysecrets.compare_digest(str(form.get("token", "")), token):
            raise HTTPException(403, "reload the page and try again")
        chosen = [s for s in form.getlist("strategy") if s in STRATEGIES]
        require = form.get("require_record") == "on"
        try:
            cfgmod.save_local({"paper": {"strategies": chosen, "require_record": require}}, config_path)
        except ValueError as e:
            return RedirectResponse(f"/?err={quote(str(e))}", 303)
        text = f"Saved: {', '.join(chosen) or 'no strategies'} will paper-trade from the next poll."
        return RedirectResponse(f"/?msg={quote(text)}", 303)

    @app.post("/poll")
    async def poll_now(request: Request):
        form = await request.form()
        if not pysecrets.compare_digest(str(form.get("token", "")), token):
            raise HTTPException(403, "reload the page and try again")
        if not busy.acquire(blocking=False):
            return RedirectResponse("/?err=" + quote("a poll is already running"), 303)
        try:
            outcome = scheduled_poll(config_path)
        finally:
            busy.release()
        return RedirectResponse("/?msg=" + quote(f"Poll: {outcome}"), 303)

    return app


def serve(config_path: str | None = None, port: int | None = None) -> None:
    import uvicorn

    cfg = cfgmod.load(config_path)
    uvicorn.run(create_app(config_path, start_scheduler=True), host="127.0.0.1", port=port or cfg.port)
