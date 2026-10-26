"""Command-line entry point: ``trademare <command>``."""

from __future__ import annotations

import argparse
import sys
from datetime import date

from pydantic import ValidationError

from trademare import __version__
from trademare.config import Secrets, load_config
from trademare.core.clock import MarketClock
from trademare.core.models import Market
from trademare.log import setup_logging


def cmd_check(args: argparse.Namespace) -> int:
    """Validate config and print a summary of each market."""
    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValidationError, ValueError) as e:
        print(f"Config error:\n{e}", file=sys.stderr)
        return 1

    secrets = Secrets()
    today = date.today()
    print(f"TradeMare {__version__} — config OK\n")
    for market, m in config.markets.items():
        status = "enabled" if m.enabled else "disabled"
        sides = "/".join(s for s, on in (("long", m.allow_long), ("short", m.allow_short)) if on)
        sl = m.stop_loss
        print(f"[{market}] {status}  broker={m.broker}  universe={m.universe}")
        print(f"  budget        {m.budget:,.0f} {market.currency} (deployable {m.deployable_capital:,.0f})")
        print(f"  directions    {sides}")
        print(
            f"  risk/trade    {m.risk_per_trade_pct}%  max position {m.max_position_pct}%  "
            f"max open {m.max_open_positions}"
        )
        print(
            f"  stop-loss     {sl.method}"
            + (f" (trailing {sl.trail_method} {sl.trail_value})" if sl.trailing else "")
        )
        ks = m.kill_switch
        print(f"  kill switch   daily -{ks.daily_loss_pct}%  drawdown -{ks.max_drawdown_pct}%")
        try:
            clock = MarketClock(market)
            print(
                f"  calendar      {market.calendar_code}  trading day: {clock.is_session(today)}  "
                f"next session: {clock.next_session(today)}"
            )
        except ValueError as e:
            print(f"  calendar      WARNING: {e}")
        print(f"  credentials   {_credential_status(m.broker, secrets)}\n")
    print(f"LLM: {'enabled (' + config.llm.provider + ')' if config.llm.enabled else 'disabled'}")
    print(f"Email: {'enabled' if config.notifications.email.enabled else 'disabled'}")
    return 0


def _credential_status(broker: str, secrets: Secrets) -> str:
    required = {
        "zerodha": ("zerodha_api_key", "zerodha_api_secret"),
        "dhan": ("dhan_client_id", "dhan_access_token"),
        "groww": ("groww_api_key",),
        "alpaca": ("alpaca_api_key", "alpaca_secret_key"),
        "paper": (),
    }[broker]
    missing = [k.upper() for k in required if getattr(secrets, k) is None]
    return "OK" if not missing else "missing " + ", ".join(missing)


def _markets(args: argparse.Namespace, config) -> list[Market]:
    if args.market:
        return [Market(args.market)]
    return list(config.enabled_markets())


def cmd_universe_refresh(args: argparse.Namespace) -> int:
    from trademare.data.universe import refresh_universe

    config = load_config(args.config)
    names = args.names or [config.markets[m].universe for m in config.enabled_markets()]
    for name in names:
        df = refresh_universe(name)
        print(f"{name}: {len(df)} symbols, {df['sector'].nunique()} sectors")
    return 0


def cmd_data_update(args: argparse.Namespace) -> int:
    from trademare.backtest.report import apply_overrides
    from trademare.data.service import DataService

    config = apply_overrides(load_config(args.config), args.set or [])
    setup_logging(config.log_level)
    service = DataService(config, Secrets())
    failed = False
    for market in _markets(args, config):
        symbols = args.symbols.split(",") if args.symbols else service.universe_symbols(market)
        if args.limit:
            symbols = symbols[: args.limit]
        if not args.skip_prices:
            report = service.update_prices(market, symbols)
            print(f"[{market}] prices: {report.summary()}")
            if report.failed:
                print(f"  failed: {', '.join(report.failed)}")
            failed |= bool(report.failed)
        if not args.skip_fundamentals:
            report = service.update_fundamentals(market, symbols, force=args.refresh_fundamentals)
            print(f"[{market}] fundamentals: {report.summary()}")
            if report.failed:
                print(f"  failed: {', '.join(report.failed)}")
    return 1 if failed and args.strict else 0


def cmd_data_status(args: argparse.Namespace) -> int:
    from trademare.data.service import DataService

    config = load_config(args.config)
    service = DataService(config, Secrets())
    for market in _markets(args, config):
        source = service.market(market).financials.source.source  # effective (after fallback)
        bar_symbols = service.store.bar_symbols(market)
        universe = service.universe_symbols(market)
        print(f"[{market}] universe {config.markets[market].universe}: {len(universe)} symbols")
        print(f"  bars cached:  {len(bar_symbols)}")
        if bar_symbols:
            idx = [service.store.read_bars(market, s).index for s in bar_symbols]
            first, last = min(i[0] for i in idx).date(), max(i[-1] for i in idx).date()
            stalest = min(i[-1] for i in idx).date()
            print(f"  bar range:    {first} .. {last} (oldest last bar {stalest})")
        with_fin = [s for s in universe if service.store.financials_updated_at(market, source, s)]
        print(f"  fundamentals: {len(with_fin)} symbols ({source})")
        missing = sorted(set(universe) - set(bar_symbols))
        if missing:
            print(f"  missing bars: {', '.join(missing[:20])}{' ...' if len(missing) > 20 else ''}")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    from trademare.core.models import Direction
    from trademare.data.service import DataService
    from trademare.strategy.scanner import run_scan, save_scan

    config = load_config(args.config)
    setup_logging(config.log_level)
    service = DataService(config, Secrets())
    asof = date.fromisoformat(args.date) if args.date else None
    for market in _markets(args, config):
        result = run_scan(service, market, asof)
        path = save_scan(service, result)
        r = result.regime
        print(f"\n[{market}] scan {result.asof} — regime {r.label} ({r.score:+.2f}); {'; '.join(r.reasons)}")
        scored = sum(1 for s in result.scores if s.composite is not None)
        print(f"  scored {scored}/{len(result.scores)} symbols -> {path}")
        for direction in Direction:
            ideas = result.ideas_for(direction)
            if not ideas:
                continue
            print(f"  {direction} ideas:")
            for i in ideas:
                lv = i.levels
                target = f"{lv.target:,.2f}" if lv.target else "—"
                print(
                    f"    {i.symbol:<12} score {i.composite:+.2f} conf {i.confidence:.2f}  "
                    f"entry {lv.entry:,.2f} stop {lv.stop:,.2f} target {target}"
                )
        if not result.ideas:
            print("  no ideas passed the strategy rules today")
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    from trademare.backtest.report import format_summary, run_backtest
    from trademare.data.service import DataService

    config = load_config(args.config)
    setup_logging("WARNING")
    start = date.fromisoformat(args.start) if args.start else None
    end = date.fromisoformat(args.end) if args.end else None
    for market in _markets(args, config):

        def progress(done: int, total: int, market: Market = market) -> None:
            if done % 50 == 0 or done == total:
                print(f"\r  [{market}] {done}/{total} sessions", end="", flush=True)

        _, meta = run_backtest(DataService(config, Secrets()), market, start, end, args.set or [], progress)
        print("\n\n" + format_summary(meta))
    return 0


def cmd_research(args: argparse.Namespace) -> int:
    from datetime import timedelta

    from trademare.backtest.diagnostics import factor_study, format_study, signal_study
    from trademare.backtest.report import apply_overrides
    from trademare.data.service import DataService
    from trademare.strategy.scanner import ScanContext

    config = apply_overrides(load_config(args.config), args.set or [])
    setup_logging("WARNING")
    for market in _markets(args, config):
        ctx = ScanContext(DataService(config, Secrets()), market)
        end = date.fromisoformat(args.end) if args.end else ctx.last_date()
        start = date.fromisoformat(args.start) if args.start else end - timedelta(days=round(365.25 * 5))
        if args.factors:
            names = [f.strip() for f in args.factors.split(",")]
            study = factor_study(ctx, start, end, names, horizon=args.horizon, step=args.step)
            print(format_study(market.value, study, f"factor study {start}..{end}") + "\n")
        else:
            study = signal_study(ctx, start, end, horizon=args.horizon, step=args.step)
            print(format_study(market.value, study, f"signal study {start}..{end}") + "\n")
    return 0


def cmd_walkforward(args: argparse.Namespace) -> int:
    from concurrent.futures import ProcessPoolExecutor

    import pandas as pd

    from trademare.backtest.report import backtests_dir
    from trademare.backtest.walkforward import format_walk_forward, walk_forward

    config = load_config(args.config)
    market = Market(args.market)
    variants: dict[str, list[str]] = {}
    for spec in args.variant:  # NAME or NAME:key=value;key=value
        name, _, rest = spec.partition(":")
        variants[name] = [kv for kv in rest.split(";") if kv]
    with ProcessPoolExecutor(max_workers=min(4, len(variants))) as pool:
        futures = {
            name: pool.submit(_backtest_job, args.config, market, args.start, args.end, overrides)
            for name, overrides in variants.items()
        }
        run_ids = {name: f.result() for name, f in futures.items()}
    curves, bench = {}, None
    for name, run_id in run_ids.items():
        frame = pd.read_parquet(backtests_dir(config) / run_id / "curves.parquet")
        curves[name] = frame["equity"]
        bench = frame.get("benchmark", bench)
        print(f"{name}: run {run_id}")
    wf = walk_forward(curves, bench, lookback_years=args.lookback)
    print("\n" + format_walk_forward(wf))
    return 0


def _backtest_job(config_path, market: Market, start: str, end: str, overrides: list[str]) -> str:
    from trademare.backtest.report import run_backtest
    from trademare.data.service import DataService

    setup_logging("WARNING")
    config = load_config(config_path)
    run_id, _ = run_backtest(
        DataService(config, Secrets()), market, date.fromisoformat(start), date.fromisoformat(end), overrides
    )
    return run_id


def cmd_telegram_setup(args: argparse.Namespace) -> int:
    from trademare.notify.telegram import find_chat_ids

    secrets = Secrets()
    if not secrets.telegram_bot_token:
        print("TELEGRAM_BOT_TOKEN is not set in .env. Create a bot with @BotFather in Telegram first.")
        return 1
    chats = find_chat_ids(secrets.telegram_bot_token.get_secret_value())
    if not chats:
        print("No messages found. Open your bot in Telegram, send it any message, then run this again.")
        return 1
    for chat_id, name in chats:
        print(f"chat_id {chat_id}  ({name})")
    print("\nAdd to config/settings.local.yaml:\nnotifications:\n  telegram:\n    enabled: true")
    print(f"    chat_id: '{chats[-1][0]}'\nthen run: trademare notify test")
    return 0


def cmd_notify_test(args: argparse.Namespace) -> int:
    from trademare.notify.telegram import send_message

    config, secrets = load_config(args.config), Secrets()
    tg = config.notifications.telegram
    if not (secrets.telegram_bot_token and tg.chat_id):
        print(
            "Set TELEGRAM_BOT_TOKEN in .env and notifications.telegram.chat_id first "
            "(trademare notify telegram-setup)."
        )
        return 1
    send_message(
        secrets.telegram_bot_token.get_secret_value(), tg.chat_id, "✅ TradeMare alerts are working."
    )
    print(
        "sent"
        + ("" if tg.enabled else " (note: notifications.telegram.enabled is false, so daily runs won't send)")
    )
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    from trademare.ops import alert_health, check_health

    config = load_config(args.config)
    report = check_health(config)
    for line in report.checked:
        print(f"ok       {line}")
    for line in report.problems:
        print(f"PROBLEM  {line}")
    if args.alert and not report.ok:
        print(alert_health(config, Secrets(), report))
    return 0 if report.ok else 1


def cmd_db_backup(args: argparse.Namespace) -> int:
    from trademare.ops import backup_database, list_backups

    config = load_config(args.config)
    print(f"backed up to {backup_database(config)}")
    print(f"{len(list_backups(config.ops.backup_dir))} backup(s) in {config.ops.backup_dir}")
    return 0


def cmd_db_restore(args: argparse.Namespace) -> int:
    from pathlib import Path

    from trademare.ops import list_backups, restore_database

    config = load_config(args.config)
    backups = list_backups(config.ops.backup_dir)
    if not args.file:
        for b in backups:
            print(b)
        print("pass one of these to restore it (stop `trademare serve` first)" if backups else "no backups")
        return 0
    path = Path(args.file)
    if not path.exists():
        print(f"not found: {path}")
        return 1
    print(f"restored {path} into {restore_database(config, path)} (previous copy kept alongside)")
    return 0


def cmd_ipo_list(args: argparse.Namespace) -> int:
    from trademare.analysis.ipo import build_cards
    from trademare.analysis.ipo_study import load_study
    from trademare.data.ipo import IpoService, ist_now

    config = load_config(args.config)
    today = ist_now().date()
    study = load_study(config.data.cache_dir)
    cards = build_cards(IpoService(config.data.cache_dir).india(today), today, study)
    for c in sorted(cards, key=lambda c: c.ipo.start or today, reverse=True):
        i, sub = c.ipo, c.ipo.subscription
        demand = ""
        if sub and sub.qib is not None:
            demand = f"QIB {sub.qib:.1f}x retail {sub.retail or 0:.1f}x total {sub.total or 0:.1f}x"
        band = f"₹{i.price_low:,.0f}-{i.price_high:,.0f}"
        print(f"{c.status:9} {i.symbol:12} {i.start} → {i.end}  {band}  {demand}  {c.label or ''}")
    if study is None:
        print("(run `trademare ipo study` for the track record of past IPOs by QIB demand)")
    return 0


def cmd_ipo_study(args: argparse.Namespace) -> int:
    from trademare.analysis.ipo_study import run_study
    from trademare.data.ipo import IpoService

    config = load_config(args.config)
    summary = run_study(IpoService(config.data.cache_dir), config.data.cache_dir)
    print(f"{summary['sample']['n']} IPOs; dropped {summary['sample']['dropped']}")
    for half, h in summary["halves"].items():
        print(f"{half}: Spearman {h['rho']:.3f} (p {h['p']:.4f}, n {h['n']})")
    print("H1 holds" if summary["h1_holds"] else "H1 fails: no labels will be shown")
    for b in summary["buckets"]:
        if b["count"]:
            print(
                f"  QIB {b['bucket']:8} n={b['count']:3}  median {b['median_gain']:+.1%}"
                f"  above issue {b['share_above']:.0%}"
            )
    return 0


def cmd_brief(args: argparse.Namespace) -> int:
    import json

    from trademare.brain import build_brief
    from trademare.data.service import DataService
    from trademare.trading.pipeline import database

    config = load_config(args.config)
    markets = [Market(args.market)] if args.market else None
    brief = build_brief(config, database(config), DataService(config, Secrets()), markets)
    if args.json:
        print(json.dumps(brief, indent=1, default=str))
        return 0
    for code, m in brief["markets"].items():
        behind = f", {m['sessions_behind']} session(s) behind" if m["sessions_behind"] else ""
        print(
            f"== {code}: prices to {m['prices_through']}{behind}; processed {m['last_processed']}; "
            f"mood {m['regime']}; {len(m['holdings'])} holding(s)"
        )
        for sig in m["pending_signals"]:
            print(
                f"  signal {sig['id']} {sig['action']} {sig['quantity']} {sig['symbol']}"
                f" @ {sig['limit_or_last_price']:,.2f}"
                f" stop {sig['stop']} · {sig['hours_left']}h left · {sig['why']}"
            )
        for h in m["holdings"]:
            print(
                f"  holding {h['symbol']} x{h['quantity']} {h['return_pct']:+.2f}%"
                f" stop {h['stop_distance_pct']}% away"
            )
        for o in m["recent_orders"][:3]:
            print(f"  order: {o}")
    for p in brief["health_problems"]:
        print(f"HEALTH: {p}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    from trademare.brain import save_review
    from trademare.trading.pipeline import database

    config = load_config(args.config)
    try:
        sig = save_review(database(config), args.signal_id, args.question, args.answer, args.risk or [])
    except KeyError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    print(
        f"review saved on {sig.market} {sig.symbol} ({sig.id}); it shows under Ask Claude on the Signals page"
    )
    return 0


def cmd_daily(args: argparse.Namespace) -> int:
    from trademare.trading.pipeline import run_daily_pipeline

    config = load_config(args.config)
    setup_logging(config.log_level)
    for market in _markets(args, config):
        r = run_daily_pipeline(
            config, market, Secrets(), update_data=not args.no_update, notify=not args.no_email
        )
        print(
            f"\n[{market}] session {r.session}: processed {', '.join(map(str, r.processed)) or 'nothing new'}"
        )
        print(f"  equity {r.equity:,.2f}  cash {r.cash:,.2f}  open positions {r.open_positions}", end="")
        print(f"  regime {r.regime}")
        if r.kill_switch:
            print(f"  KILL SWITCH ACTIVE: {r.kill_reason} (re-arm with: trademare rearm -m {market})")
        for f in r.fills:
            print(f"  fill: {f}")
        for sig in r.signals:
            print(f"  new {sig.action} {sig.direction} {sig.symbol} x{sig.quantity}", end="")
            print(f" @ {sig.entry_price:,.2f}  [{sig.id}]")
        for n in r.notes:
            print(f"  note: {n}")
    return 0


def _desk_for_signal(config, signal_id: str):
    from trademare.data.service import DataService
    from trademare.storage.db import SignalRow
    from trademare.trading.desk import TradingDesk
    from trademare.trading.pipeline import database

    db = database(config)
    with db.session() as s:
        sig = s.get(SignalRow, signal_id)
        if sig is None:
            raise SystemExit(f"no signal {signal_id}")
        market = Market(sig.market)
    return TradingDesk(DataService(config, Secrets()), db, market, Secrets())


def cmd_signals(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from trademare.storage.db import SignalRow
    from trademare.trading.pipeline import database

    config = load_config(args.config)
    with database(config).session() as s:
        q = (
            select(SignalRow)
            .where(SignalRow.status == "PENDING")
            .order_by(SignalRow.market, SignalRow.score.desc())
        )
        rows = [r for r in s.scalars(q) if not args.market or r.market == args.market]
    if not rows:
        print("no pending signals")
    for r in rows:
        stop = f" stop {r.stop_price:,.2f}" if r.stop_price else ""
        print(
            f"[{r.market}] {r.id}  {r.action:<5} {r.direction:<5} {r.symbol:<12} x{r.quantity:<6} "
            f"@ {r.entry_price:,.2f}{stop}  score {r.score:+.2f}  expires {r.expires_at:%Y-%m-%d %H:%M} UTC"
        )
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    from trademare.trading.desk import DeskError

    desk = _desk_for_signal(load_config(args.config), args.signal_id)
    try:
        order = desk.approve(
            args.signal_id, quantity=args.qty, limit=args.limit, stop=args.stop, note=args.note
        )
    except DeskError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    print(
        f"approved: {order.side} {order.quantity} {order.symbol} {order.order_type}"
        f"{f' @ {order.limit_price:,.2f}' if order.limit_price else ''} for {order.valid_until} [{order.id}]"
    )
    return 0


def cmd_reject(args: argparse.Namespace) -> int:
    from trademare.trading.desk import DeskError

    desk = _desk_for_signal(load_config(args.config), args.signal_id)
    try:
        desk.reject(args.signal_id, args.note)
    except DeskError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    print("rejected")
    return 0


def cmd_rearm(args: argparse.Namespace) -> int:
    from trademare.data.service import DataService
    from trademare.trading.desk import TradingDesk
    from trademare.trading.pipeline import database

    config = load_config(args.config)
    market = Market(args.market)
    TradingDesk(DataService(config, Secrets()), database(config), market, Secrets()).rearm()
    print(f"[{market}] kill switch re-armed")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from trademare.scheduler import build_scheduler, catch_up
    from trademare.web.app import create_app

    config = load_config(args.config)
    setup_logging(config.log_level)
    scheduler = build_scheduler(config, args.config)
    scheduler.start()
    for job in scheduler.get_jobs():
        print(f"scheduled {job.id}: next run {job.next_run_time}")
    for market in catch_up(config, args.config):
        print(f"[{market}] behind on sessions: catching up in the background")
    print(f"TradeMare dashboard: http://{args.host}:{args.port}")
    try:
        uvicorn.run(
            create_app(config, config_path=args.config), host=args.host, port=args.port, log_level="warning"
        )
    finally:
        scheduler.shutdown(wait=False)
    return 0


def cmd_broker_login(args: argparse.Namespace) -> int:
    from datetime import datetime
    from urllib.parse import parse_qs, urlparse
    from zoneinfo import ZoneInfo

    from trademare.execution.brokers.tokens import save_token
    from trademare.execution.brokers.zerodha import ZerodhaBroker

    config = load_config(args.config)
    broker = ZerodhaBroker(Market.IN, config.markets[Market.IN], Secrets())
    print("1. Open this URL and log in to Kite:\n   " + broker.login_url())
    print("2. After login you are redirected to your app's redirect URL.")
    raw = input("3. Paste the full redirect URL (or just the request_token): ").strip()
    token = parse_qs(urlparse(raw).query).get("request_token", [raw])[0]
    access = broker.exchange_request_token(token)
    path = save_token("zerodha", access, datetime.now(ZoneInfo("Asia/Kolkata")).date())
    print(f"Logged in. Session saved to {path} (valid until ~06:00 IST tomorrow).")
    return 0


def cmd_broker_holdings(args: argparse.Namespace) -> int:
    from trademare.data.service import DataService
    from trademare.execution import outside_holdings
    from trademare.trading.pipeline import database

    config = load_config(args.config)
    secrets = Secrets()
    try:
        outside_holdings.sync(config, secrets, database(config))
    except Exception as e:
        print(f"could not read Zerodha holdings: {e}", file=sys.stderr)
        return 1
    v = outside_holdings.view(config, DataService(config, secrets))
    for r in v["rows"]:
        print(
            f"{r['symbol']:12} x{r['quantity']:<5} avg {r['average_price']:>10,.2f}  now {r['price']:>10,.2f}"
            f"  {r['return'] * 100:+6.1f}%  rank {r['rank'] or '-'}"
        )
    print(f"total: invested {v['invested']:,.0f}, now {v['value']:,.0f} ({v['return'] * 100:+.1f}%)")
    print("shown on the India portfolio page (read-only; TradeMare never trades these)")
    return 0


def cmd_broker_check(args: argparse.Namespace) -> int:
    from trademare.execution.brokers import BrokerError, create_broker
    from trademare.storage.db import PositionRow
    from trademare.trading.pipeline import database

    config = load_config(args.config)
    status = 0
    for market in _markets(args, config):
        cfg = config.markets[market]
        broker = create_broker(market, cfg, Secrets())
        mode = (
            "simulated"
            if broker.simulated
            else ("validated" if cfg.validated else "NOT validated: orders blocked")
        )
        print(f"[{market}] broker {broker.name} ({mode})")
        if broker.simulated:
            continue
        try:
            broker.connect()
            funds = broker.get_funds()
            held = {p.symbol: p.quantity for p in broker.get_positions()}
        except BrokerError as e:
            print(f"  ERROR: {e}")
            status = 1
            continue
        print(f"  cash available {funds.cash_available:,.2f}")
        with database(config).session() as s:
            ours = {
                p.symbol: p.quantity
                for p in s.query(PositionRow).filter_by(market=market.value, status="open")
            }
        for symbol in sorted(set(held) | set(ours)):
            a, b = held.get(symbol, 0), ours.get(symbol, 0)
            flag = "" if a == b else "  <-- MISMATCH"
            print(f"  {symbol:<14} broker {a:>6}  trademare {b:>6}{flag}")
        if not held and not ours:
            print("  no positions at broker or in TradeMare")
    return status


def cmd_web(args: argparse.Namespace) -> int:
    import uvicorn

    from trademare.web.app import create_app

    config = load_config(args.config)
    setup_logging(config.log_level)
    if args.host not in ("127.0.0.1", "localhost"):
        print("WARNING: dashboard has no login yet; exposing it beyond localhost is unsafe.", file=sys.stderr)
    print(f"TradeMare dashboard: http://{args.host}:{args.port}")
    uvicorn.run(
        create_app(config, config_path=args.config), host=args.host, port=args.port, log_level="warning"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trademare", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("-c", "--config", help="path to settings.yaml (default: config/settings.yaml)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="validate config and show market summary").set_defaults(func=cmd_check)

    universe = sub.add_parser("universe", help="manage symbol universes").add_subparsers(
        dest="sub", required=True
    )
    refresh = universe.add_parser("refresh", help="download index constituents to config/universes/")
    refresh.add_argument("names", nargs="*", help="universe names (default: those used by enabled markets)")
    refresh.set_defaults(func=cmd_universe_refresh)

    data = sub.add_parser("data", help="download and inspect market data").add_subparsers(
        dest="sub", required=True
    )
    update = data.add_parser("update", help="refresh cached prices and fundamentals")
    update.add_argument("-m", "--market", choices=[m.value for m in Market])
    update.add_argument("-s", "--symbols", help="comma-separated symbols (default: whole universe)")
    update.add_argument("--limit", type=int, help="only the first N symbols (for quick tests)")
    update.add_argument("--skip-prices", action="store_true")
    update.add_argument("--skip-fundamentals", action="store_true")
    update.add_argument(
        "--refresh-fundamentals", action="store_true", help="re-download fundamentals even if recently cached"
    )
    update.add_argument("--set", action="append", metavar="KEY=VALUE", help="override config for this run")
    update.add_argument("--strict", action="store_true", help="exit non-zero if any symbol fails")
    update.set_defaults(func=cmd_data_update)
    status = data.add_parser("status", help="show what is cached")
    status.add_argument("-m", "--market", choices=[m.value for m in Market])
    status.set_defaults(func=cmd_data_status)

    scan = sub.add_parser("scan", help="score the universe and propose trade ideas (cached data only)")
    scan.add_argument("-m", "--market", choices=[m.value for m in Market])
    scan.add_argument("--date", help="as-of date YYYY-MM-DD (default: latest cached session)")
    scan.set_defaults(func=cmd_scan)

    bt = sub.add_parser("backtest", help="replay the strategy over history (cached data only)")
    bt.add_argument("-m", "--market", choices=[m.value for m in Market])
    bt.add_argument("--start", help="YYYY-MM-DD (default: backtest.years before --end)")
    bt.add_argument("--end", help="YYYY-MM-DD (default: latest cached session)")
    bt.add_argument(
        "--set", action="append", metavar="KEY=VALUE",
        help="override config for this run, e.g. --set strategy.min_composite_score=0.5",
    )  # fmt: skip
    bt.set_defaults(func=cmd_backtest)

    research = sub.add_parser("research", help="measure whether analyst scores predict forward returns")
    research.add_argument("-m", "--market", choices=[m.value for m in Market])
    research.add_argument("--start", help="YYYY-MM-DD (default: 5 years before --end)")
    research.add_argument("--end", help="YYYY-MM-DD (default: latest cached session)")
    research.add_argument("--horizon", type=int, default=20, help="forward return horizon in sessions")
    research.add_argument("--step", type=int, default=5, help="sample every N sessions")
    research.add_argument(
        "--factors",
        help="comma-separated factor names to study instead of the strategy scores, e.g. mom_12_1,low_vol",
    )
    research.add_argument("--set", action="append", metavar="KEY=VALUE", help="override config for this run")
    research.set_defaults(func=cmd_research)

    wf = sub.add_parser("walkforward", help="run strategy variants and chain yearly trailing-Sharpe picks")
    wf.add_argument("-m", "--market", required=True, choices=[m.value for m in Market])
    wf.add_argument("--start", required=True, help="YYYY-MM-DD")
    wf.add_argument("--end", required=True, help="YYYY-MM-DD")
    wf.add_argument(
        "--variant", action="append", required=True, metavar="NAME[:key=value;...]",
        help="a variant and its config overrides, e.g. V1:strategy.vol_target=true",
    )  # fmt: skip
    wf.add_argument(
        "--lookback", type=int, default=3, help="years of history used to pick each year's variant"
    )
    wf.set_defaults(func=cmd_walkforward)

    notify = sub.add_parser("notify", help="phone alerts").add_subparsers(dest="notify_cmd", required=True)
    notify.add_parser("telegram-setup", help="find your Telegram chat id").set_defaults(
        func=cmd_telegram_setup
    )
    notify.add_parser("test", help="send a test message").set_defaults(func=cmd_notify_test)

    health = sub.add_parser(
        "health", help="check for missed daily runs, a tripped kill switch, stale backups"
    )
    health.add_argument("--alert", action="store_true", help="send problems by Telegram/email (once a day)")
    health.set_defaults(func=cmd_health)

    dbp = sub.add_parser("db", help="database backups").add_subparsers(dest="sub", required=True)
    dbp.add_parser("backup", help="back up the database now").set_defaults(func=cmd_db_backup)
    restore = dbp.add_parser("restore", help="list backups, or restore one (stop the server first)")
    restore.add_argument("file", nargs="?", help="backup file to restore")
    restore.set_defaults(func=cmd_db_restore)

    brief = sub.add_parser(
        "brief", help="snapshot for a reviewer: freshness, signals, holdings, orders, health"
    )
    brief.add_argument("--json", action="store_true", help="machine-readable output")
    brief.add_argument("-m", "--market", choices=["IN", "US"])
    brief.set_defaults(func=cmd_brief)

    review = sub.add_parser("review", help="save a written review on a signal (shown under Ask Claude)")
    review.add_argument("signal_id")
    review.add_argument("--question", default="Should I take this trade?")
    review.add_argument("--answer", required=True)
    review.add_argument("--risk", action="append", help="a risk to list (repeatable)")
    review.set_defaults(func=cmd_review)

    ipo = sub.add_parser("ipo", help="Indian IPOs: list current issues, or run the demand study")
    ipo_sub = ipo.add_subparsers(dest="ipo_cmd")
    ipo.set_defaults(func=cmd_ipo_list)
    ipo_sub.add_parser("list", help="open, upcoming and recent mainboard IPOs").set_defaults(
        func=cmd_ipo_list
    )
    ipo_sub.add_parser(
        "study", help="re-run the pre-registered study of QIB demand vs listing gains (slow the first time)"
    ).set_defaults(func=cmd_ipo_study)

    daily = sub.add_parser("daily", help="run the daily process now: data, paper fills, signals, digest")
    daily.add_argument("-m", "--market", choices=[m.value for m in Market])
    daily.add_argument("--no-update", action="store_true", help="skip the data download")
    daily.add_argument("--no-email", action="store_true", help="skip the digest")
    daily.set_defaults(func=cmd_daily)

    signals = sub.add_parser("signals", help="list pending signals")
    signals.add_argument("-m", "--market", choices=[m.value for m in Market])
    signals.set_defaults(func=cmd_signals)

    approve = sub.add_parser("approve", help="approve a pending signal (places the order)")
    approve.add_argument("signal_id")
    approve.add_argument("--qty", type=int, help="override quantity")
    approve.add_argument("--limit", type=float, help="override entry limit price")
    approve.add_argument("--stop", type=float, help="override stop price")
    approve.add_argument("--note")
    approve.set_defaults(func=cmd_approve)

    reject = sub.add_parser("reject", help="reject a pending signal")
    reject.add_argument("signal_id")
    reject.add_argument("--note")
    reject.set_defaults(func=cmd_reject)

    rearm = sub.add_parser("rearm", help="re-arm a tripped kill switch")
    rearm.add_argument("-m", "--market", choices=[m.value for m in Market], required=True)
    rearm.set_defaults(func=cmd_rearm)

    serve = sub.add_parser("serve", help="dashboard + scheduled daily runs (keep this running)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=cmd_serve)

    broker = sub.add_parser("broker", help="broker sessions and reconciliation").add_subparsers(
        dest="sub", required=True
    )
    login = broker.add_parser("login", help="daily Zerodha Kite login (stores today's access token)")
    login.add_argument("name", choices=["zerodha"])
    login.set_defaults(func=cmd_broker_login)
    broker.add_parser(
        "holdings", help="read your Zerodha holdings (bought outside TradeMare) for the dashboard"
    ).set_defaults(func=cmd_broker_holdings)
    check = broker.add_parser("check", help="connect, show funds, compare broker holdings with TradeMare")
    check.add_argument("-m", "--market", choices=[m.value for m in Market])
    check.set_defaults(func=cmd_broker_check)

    web = sub.add_parser("web", help="run the local dashboard")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8000)
    web.set_defaults(func=cmd_web)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    sys.exit(args.func(args))
