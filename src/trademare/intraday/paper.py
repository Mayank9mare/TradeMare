"""Live paper trading: re-simulate today's candles every few minutes and keep a ledger.

Because the simulator is causal, running it on today's candles so far gives exactly the trades
an order-by-order engine would have made by now (with the study's rules). On top of that the
paper account applies sizing (``risk_pct`` of capital per trade, no leverage) and the limit on
open positions. Trades still running show as open; after 15:30 the day is stored in the module's
own SQLite database. Never sends orders anywhere.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Callable
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd

from trademare.intraday import costs, universe
from trademare.intraday import features as feat
from trademare.intraday import study as study_mod
from trademare.intraday.sim import Rules, Trade, simulate
from trademare.intraday.store import CandleStore
from trademare.intraday.strategies import STRATEGIES, evaluate

HISTORY_SESSIONS = 70  # enough for the 200-candle average, pivots and the 60-day CPR rank
DAY_END = time(15, 30)


def allowed_strategies(cfg, record: dict | None) -> tuple[list[str], list[str]]:
    """(strategies that may paper-trade, chosen ones held back by the evidence bar)."""
    chosen = [s for s in cfg.paper.strategies if s in STRATEGIES]
    if not cfg.paper.require_record:
        return chosen, []
    records = (record or {}).get("records", {})
    ok = [s for s in chosen if records.get(s, {}).get("passes")]
    return ok, [s for s in chosen if s not in ok]


def size(cfg, entry: float, stop: float) -> int:
    risk_amount = cfg.paper.capital * cfg.paper.risk_pct / 100
    qty = math.floor(risk_amount / abs(entry - stop)) if entry != stop else 0
    cap = math.floor(cfg.paper.capital * cfg.paper.max_position_pct / 100 / entry)
    return max(0, min(qty, cap))


def ledger(cfg, trades: list[Trade], live: bool) -> list[dict]:
    """Size trades in entry order, skipping any that would exceed the open-position limit.
    ``live``: trades that ran out of candles today are still open (marked at the last price)."""
    rows: list[dict] = []
    for t in sorted(trades, key=lambda t: (t.entry_time, t.strategy, t.symbol)):
        open_now = [r for r in rows if r["taken"] and r["entry_time"] <= t.entry_time < r["exit_time"]]
        row = asdict(t)
        row["status"] = "open" if (live and t.reason == "end") else "closed"
        if len(open_now) >= cfg.paper.max_positions:
            row.update(
                taken=False, qty=0, pnl=0.0, note=f"skipped: {cfg.paper.max_positions} positions already open"
            )
            rows.append(row)
            continue
        qty = size(cfg, t.entry, t.stop)
        if qty <= 0:
            row.update(taken=False, qty=0, pnl=0.0, note="skipped: too small to size")
            rows.append(row)
            continue
        charges = costs.round_trip(t.entry, t.exit, qty, t.side > 0)
        pnl = t.side * (t.exit - t.entry) * qty - charges
        row.update(taken=True, qty=qty, pnl=round(pnl, 2), charges=round(charges, 2), note="")
        rows.append(row)
    return rows


def day_trades(
    cfg, store: CandleStore, symbols: list[str], strategy_ids: list[str], day: date
) -> list[Trade]:
    rules = Rules.from_config(cfg)
    out: list[Trade] = []
    for symbol in symbols:
        candles = store.read(symbol)
        if candles is None or candles.empty or candles.index[-1].date() < day:
            continue
        days = candles.index.normalize().unique()
        keep = days[days <= pd.Timestamp(day)][-HISTORY_SESSIONS:]
        recent = candles[
            (candles.index.normalize() >= keep[0]) & (candles.index.normalize() <= pd.Timestamp(day))
        ]
        f = feat.build(recent, cfg.minutes)
        for sid in strategy_ids:
            out += [
                t for t in simulate(sid, symbol, f, evaluate(STRATEGIES[sid], f), rules) if t.day == str(day)
            ]
    return out


# ---------------------------------------------------------------- persistence (own database)
def db_path(cfg) -> Path:
    return cfg.root / "intraday.db"


def _db(cfg) -> sqlite3.Connection:
    cfg.root.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path(cfg))
    con.execute(
        "create table if not exists paper_trades (day text, strategy text, symbol text, side integer, "
        "entry_time text, entry real, stop real, target real, exit_time text, exit real, reason text, "
        "qty integer, pnl real, charges real)"
    )
    con.execute("create table if not exists polls (ts text, ok integer, message text)")
    return con


def store_day(cfg, day: date, rows: list[dict]) -> None:
    with _db(cfg) as con:
        con.execute("delete from paper_trades where day = ?", (str(day),))
        con.executemany(
            "insert into paper_trades values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (str(day), r["strategy"], r["symbol"], r["side"], r["entry_time"], r["entry"], r["stop"],
                 r["target"], r["exit_time"], r["exit"], r["reason"], r["qty"], r["pnl"], r.get("charges", 0))
                for r in rows
                if r["taken"]
            ],
        )  # fmt: skip


def log_poll(cfg, ok: bool, message: str) -> None:
    with _db(cfg) as con:
        con.execute(
            "insert into polls values (?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), int(ok), message),
        )


def history(cfg, days: int = 60) -> list[dict]:
    if not db_path(cfg).exists():
        return []
    with _db(cfg) as con:
        rows = con.execute(
            "select day, count(*), sum(pnl), sum(pnl > 0) from paper_trades "
            "group by day order by day desc limit ?",
            (days,),
        ).fetchall()
    return [{"day": d, "trades": n, "pnl": round(p or 0, 2), "wins": w} for d, n, p, w in rows]


def last_polls(cfg, n: int = 5) -> list[dict]:
    if not db_path(cfg).exists():
        return []
    with _db(cfg) as con:
        rows = con.execute("select ts, ok, message from polls order by ts desc limit ?", (n,)).fetchall()
    return [{"ts": ts, "ok": bool(ok), "message": m} for ts, ok, m in rows]


def today_path(cfg) -> Path:
    return cfg.root / "today.json"


def load_today(cfg) -> dict | None:
    p = today_path(cfg)
    return json.loads(p.read_text()) if p.exists() else None


# ---------------------------------------------------------------- one poll
def poll(cfg, fetch: Callable[[str, datetime, datetime], pd.DataFrame], now: datetime) -> dict:
    """Fetch today's candles, re-simulate, save today's ledger; store the day after the close.

    ``fetch(symbol, start, end)`` returns candles (Kite in production, fakes in tests)."""
    day = now.date()
    store = CandleStore(cfg.root, cfg.interval)
    symbols = universe.load(cfg.root, cfg.universe)["symbol"].tolist()
    record = study_mod.load(cfg)
    run, held_back = allowed_strategies(cfg, record)
    errors = []
    start = datetime.combine(day, time(9, 15))
    for symbol in symbols:
        try:
            new = fetch(symbol, start, now)
        except Exception as e:  # one stock failing must not stop the others
            errors.append(f"{symbol}: {e}")
            continue
        # Only finished candles: the one still forming would leak the future into "its close".
        new = new[new.index + timedelta(minutes=cfg.minutes) <= pd.Timestamp(now)]
        if len(new):
            store.merge(symbol, new)
    trades = day_trades(cfg, store, symbols, run, day) if run else []
    live = now.time() < DAY_END
    rows = ledger(cfg, trades, live)
    taken = [r for r in rows if r["taken"]]
    result = {
        "as_of": now.isoformat(timespec="seconds"),
        "day": str(day),
        "strategies": run,
        "held_back": held_back,
        "errors": errors[:10],
        "rows": rows,
        "realised": round(sum(r["pnl"] for r in taken if r["status"] == "closed"), 2),
        "open_pnl": round(sum(r["pnl"] for r in taken if r["status"] == "open"), 2),
    }
    today_path(cfg).parent.mkdir(parents=True, exist_ok=True)
    today_path(cfg).write_text(json.dumps(result, indent=1, default=str))
    if not live:
        store_day(cfg, day, rows)
    msg = f"{len(symbols) - len(errors)}/{len(symbols)} stocks, {len(taken)} paper trades"
    log_poll(cfg, not errors, msg + (f"; {len(errors)} errors" if errors else ""))
    return result
