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
    from trademare.data.service import DataService

    config = load_config(args.config)
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
            report = service.update_fundamentals(market, symbols)
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


def cmd_web(args: argparse.Namespace) -> int:
    import uvicorn

    from trademare.web.app import create_app

    config = load_config(args.config)
    setup_logging(config.log_level)
    if args.host not in ("127.0.0.1", "localhost"):
        print("WARNING: dashboard has no login yet; exposing it beyond localhost is unsafe.", file=sys.stderr)
    print(f"TradeMare dashboard: http://{args.host}:{args.port}")
    uvicorn.run(create_app(config), host=args.host, port=args.port, log_level="warning")
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

    web = sub.add_parser("web", help="run the local dashboard")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8000)
    web.set_defaults(func=cmd_web)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    sys.exit(args.func(args))
