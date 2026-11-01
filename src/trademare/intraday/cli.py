"""``trademare-intraday``: the intraday module's own command (independent of ``trademare``)."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, time, timedelta

from trademare.intraday import config as cfgmod
from trademare.intraday import universe


def cmd_universe(args: argparse.Namespace) -> int:
    cfg = cfgmod.load(args.config)
    df = universe.refresh(cfg.root, cfg.universe)
    print(f"{cfg.universe}: {len(df)} symbols saved to {universe.path(cfg.root, cfg.universe)}")
    return 0


def cmd_data_update(args: argparse.Namespace) -> int:
    from trademare.intraday.kite_data import KiteCandles, KiteDataError, kite_client
    from trademare.intraday.store import CandleStore

    cfg = cfgmod.load(args.config)
    symbols = args.symbol or universe.load(cfg.root, cfg.universe)["symbol"].tolist()
    store = CandleStore(cfg.root, cfg.interval)
    try:
        candles = KiteCandles(kite_client(), cfg.root)
    except KiteDataError as e:
        print(e)
        return 1
    end = datetime.now().replace(second=0, microsecond=0)
    failed = []
    for i, symbol in enumerate(symbols, 1):
        have = store.read(symbol)
        if have is not None and len(have) and not args.full:
            start = (
                have.index[-1] - timedelta(days=1)
            ).to_pydatetime()  # re-fetch the last day: it may be partial
        else:
            start = datetime.combine(args.start or cfg.history_start, time(9, 15))
        try:
            new = candles.fetch(symbol, start, end, cfg.interval)
        except KiteDataError as e:
            failed.append(f"{symbol}: {e}")
            print(f"[{i}/{len(symbols)}] {symbol}: failed ({e})")
            continue
        full = store.merge(symbol, new) if len(new) else (have if have is not None else new)
        first = full.index[0].date() if len(full) else "-"
        print(f"[{i}/{len(symbols)}] {symbol}: +{len(new)} candles, {len(full)} total from {first}")
    if failed:
        print(f"{len(failed)} symbol(s) failed; re-run to retry")
        return 1
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from trademare.intraday.store import CandleStore

    cfg = cfgmod.load(args.config)
    store = CandleStore(cfg.root, cfg.interval)
    symbols = store.symbols()
    print(f"settings: {args.config or cfgmod.DEFAULT_PATH}; data: {cfg.root}; interval {cfg.interval}")
    if not symbols:
        print("no candles yet: run `trademare-intraday universe`, then `trademare-intraday data update`")
        return 0
    spans = [(s, b.index[0], b.index[-1], len(b)) for s in symbols if (b := store.read(s)) is not None]
    print(f"{len(spans)} symbols; earliest {min(x[1] for x in spans)}; latest {max(x[2] for x in spans)}")
    print(f"paper strategies: {cfg.paper.strategies or 'none chosen'}")
    return 0


def _pct(x) -> str:
    return "    -   " if x is None else f"{x * 100:+.3f}%"


def cmd_study(args: argparse.Namespace) -> int:
    from trademare.intraday import study
    from trademare.intraday.strategies import STRATEGIES

    cfg = cfgmod.load(args.config)
    ids = args.strategy or None
    if ids and (bad := [s for s in ids if s not in STRATEGIES]):
        print(f"unknown strategy {bad}; choose from {', '.join(STRATEGIES)}")
        return 1
    if args.show:
        result = study.load(cfg)
        if result is None:
            print("no study yet: run `trademare-intraday study`")
            return 1
    else:
        try:
            result = study.run(cfg, ids, progress=lambda m: print(m, end="\r"))
        except FileNotFoundError as e:
            print(e)
            return 1
        study.save(cfg, result)
        print()
    print(f"candles {result['data_from']} to {result['data_through']}, {result['symbols']} stocks")
    for sid, r in result["records"].items():
        cells = []
        for p in study.PERIODS:
            st = r["periods"][p]
            if not st["trades"]:
                cells.append(f"{p}: no trades")
                continue
            cells.append(
                f"{p}: {st['trades']:5d} tr {st['win_rate'] * 100:3.0f}% won avg {_pct(st['avg_ret'])}"
            )
        print(f"{sid:17} " + " | ".join(cells) + f" | {'pass' if r['passes'] else 'fail'}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from trademare.intraday.server import serve

    serve(args.config, args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="trademare-intraday", description="Intraday research and paper trading")
    p.add_argument("--config", default=None, help="settings file (default config/intraday.yaml)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser(
        "universe", help="download the stock list (Nifty 50) into the module's folder"
    ).set_defaults(func=cmd_universe)
    data = sub.add_parser("data", help="5-minute candles from Kite Connect").add_subparsers(
        dest="data_cmd", required=True
    )
    up = data.add_parser("update", help="download new candles (history the first time; needs a Kite login)")
    up.add_argument("-s", "--symbol", action="append", help="only this symbol (repeatable)")
    up.add_argument("--start", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(), help="first day")
    up.add_argument("--full", action="store_true", help="re-download everything from the start date")
    up.set_defaults(func=cmd_data_update)
    sub.add_parser("status", help="what data and settings the module has").set_defaults(func=cmd_status)
    st = sub.add_parser("study", help="track record of each strategy on the cached candles (minutes)")
    st.add_argument("-s", "--strategy", action="append", help="only this strategy (repeatable)")
    st.add_argument("--show", action="store_true", help="print the saved study without re-running it")
    st.set_defaults(func=cmd_study)
    sv = sub.add_parser("serve", help="intraday dashboard and paper trading during market hours (port 8766)")
    sv.add_argument("--port", type=int, default=None)
    sv.set_defaults(func=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
