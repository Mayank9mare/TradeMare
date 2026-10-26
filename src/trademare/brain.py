"""One snapshot of everything a reviewer needs, for the `trademare-brain` Claude Code skill.

`trademare brief` prints it (as JSON with --json). It reads only the local database and caches:
no network, no orders. Reviews written back with `trademare review` are stored as the same "ask"
events the dashboard's Ask Claude panel shows, so a Claude Code session's verdict appears on the
signal card.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select

from trademare.config.schema import AppConfig
from trademare.core.clock import MarketClock
from trademare.core.models import Market
from trademare.storage.db import Database, EventRow, MarketStateRow, OrderRow, PositionRow, SignalRow

MAX_EVIDENCE_REASONS = 6


def _day(d: date | datetime | None) -> str | None:
    return d.isoformat() if d else None


def _signal(sig: SignalRow, now: datetime, sector: str | None = None) -> dict[str, Any]:
    from trademare.web.reasons import split_reasons

    ev = sig.evidence or {}
    rs = split_reasons(sig.reasons, sig.direction)
    exp = (
        sig.expires_at.replace(tzinfo=UTC) if sig.expires_at and not sig.expires_at.tzinfo else sig.expires_at
    )
    risk = ev.get("risk") or {}
    events = ev.get("events") or {}
    fund = ev.get("fundamental") or {}
    return {
        "id": sig.id,
        "market": sig.market,
        "symbol": sig.symbol,
        "action": "buy" if (sig.action == "ENTRY") == (sig.direction == "LONG") else "sell",
        "quantity": sig.quantity,
        "limit_or_last_price": sig.entry_price,
        "stop": sig.stop_price,
        "cost": round(sig.quantity * sig.entry_price, 2),
        "most_you_can_lose": round(abs(sig.entry_price - sig.stop_price) * sig.quantity, 2)
        if sig.stop_price
        else None,
        "share_of_capital": risk.get("position_pct"),
        "score": sig.score,
        "scan_date": _day(sig.scan_date),
        "decide_by_utc": exp.isoformat() if exp else None,
        "hours_left": round((exp - now).total_seconds() / 3600, 1) if exp else None,
        "exit_reason": sig.exit_reason,
        "why": rs.summary,
        "for": rs.pro,
        "against": rs.con,
        "also_noted": rs.neutral,
        "warnings": (sig.reasons or {}).get("events", []) + (sig.reasons or {}).get("tax", []),
        "sector": sector,
        "next_results": events.get("next_results"),
        "sessions_to_results": events.get("sessions_to_results"),
        "flags": events.get("flags") or [],  # red-flag / caution findings from filings or news
        "red_flag_count": events.get("red") or 0,
        "amber_flag_count": events.get("amber") or 0,
        "filings_coverage": events.get("coverage"),
        "recent_filings": (events.get("recent") or [])[:MAX_EVIDENCE_REASONS],
        "forensic": fund.get("forensic"),
        "fundamental_metrics": fund.get("metrics"),
        "technical_metrics": (ev.get("technical") or {}).get("metrics"),
    }


def build_brief(
    config: AppConfig, db: Database, service, markets: list[Market] | None = None
) -> dict[str, Any]:
    from trademare.analysis.ipo_study import load_study
    from trademare.ops import check_health
    from trademare.strategy.scanner import load_latest_scan
    from trademare.trading.outcomes import describe
    from trademare.web.overview_data import ideas_vs_signals, market_status

    store = service.store
    now = datetime.now(UTC)
    out: dict[str, Any] = {"generated_at_utc": now.isoformat(), "markets": {}}
    for market, cfg in config.markets.items():
        if not cfg.enabled or (markets and market not in markets):
            continue
        status = market_status(market, cfg, store=store)
        clock = MarketClock(market)
        with db.session() as s:
            state = s.get(MarketStateRow, market.value)
            pending = list(
                s.scalars(
                    select(SignalRow)
                    .where(SignalRow.market == market.value, SignalRow.status == "PENDING")
                    .order_by(SignalRow.score.desc())
                )
            )
            positions = list(
                s.scalars(
                    select(PositionRow).where(
                        PositionRow.market == market.value, PositionRow.status == "open"
                    )
                )
            )
            recent_orders = list(
                s.scalars(
                    select(OrderRow)
                    .where(OrderRow.market == market.value, OrderRow.purpose.in_(("entry", "exit")))
                    .order_by(OrderRow.updated_at.desc())
                    .limit(8)
                )
            )
            s.expunge_all()
        holdings = []
        for p in positions:
            last = p.last_price or p.entry_price
            sign = 1 if p.direction == "LONG" else -1
            holdings.append({
                "symbol": p.symbol,
                "sector": p.sector,
                "quantity": p.quantity,
                "entry_price": p.entry_price,
                "entry_date": _day(p.entry_date),
                "last_price": last,
                "return_pct": round(sign * (last / p.entry_price - 1) * 100, 2),
                "unrealised_pnl": round(sign * (last - p.entry_price) * p.quantity - (p.entry_costs or 0), 2),
                "stop": p.stop_price,
                "stop_distance_pct": round((p.stop_price / last - 1) * 100, 2) if p.stop_price else None,
                "days_held": (date.today() - p.entry_date).days,
            })  # fmt: skip
        live = [s for s in pending if not s.expires_at or s.expires_at.replace(tzinfo=UTC) > now]
        sectors = _sectors(cfg.universe)
        try:
            scan = load_latest_scan(service, market)
        except Exception:  # an unreadable scan file should not stop the brief
            scan = None
        out["markets"][market.value] = {
            "exchange_open": status.is_open,
            "paper": not cfg.validated,
            "strategy": str(getattr(cfg.strategy_mode, "value", cfg.strategy_mode)),
            "prices_through": _day(status.prices_through),
            "sessions_behind": status.behind,
            "last_processed": _day(state.last_processed) if state else None,
            "last_finished_session": _day(clock.last_completed_session()),
            "next_daily_run": status.next_run.isoformat() if status.next_run else None,
            "kill_switch_tripped": bool(state and state.kill_switch),
            "cash": state.cash if state else None,
            "regime": scan.regime.label.value if scan else None,
            "ideas_vs_signals": ideas_vs_signals(db, config, market, scan.asof) if scan else None,
            "pending_signals": [_signal(s, now, sectors.get(s.symbol)) for s in live],
            "holdings": holdings,
            "recent_orders": [describe(o, store)[1] for o in recent_orders],
            "top_ideas": [
                {"symbol": i.symbol, "score": round(i.composite, 2), "sector": i.sector}
                for i in (scan.ideas[:10] if scan else [])
            ],
        }
    from trademare.execution import outside_holdings

    outside = outside_holdings.view(config, service)
    if outside:
        out["outside_holdings_IN"] = {
            "note": "bought outside TradeMare; read-only, never traded by the system",
            "as_of": outside["fetched_at"].isoformat(),
            "invested": round(outside["invested"]),
            "value": round(outside["value"]),
            "return_pct": round(outside["return"] * 100, 1) if outside["return"] is not None else None,
            "rows": [
                {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()}
                for r in outside["rows"]
            ],
        }
    health = check_health(config)
    out["health_problems"] = health.problems
    study = load_study(config.data.cache_dir)
    out["ipo_study_available"] = study is not None
    return out


def _sectors(universe: str) -> dict[str, str]:
    from trademare.data.universe import load_universe

    try:
        u = load_universe(universe)
        return dict(zip(u["symbol"], u["sector"].fillna(""), strict=True))
    except Exception:  # a missing universe file only costs the sector labels
        return {}


def save_review(db: Database, signal_id: str, question: str, answer: str, risks: list[str]) -> SignalRow:
    """Store a review on a signal so it shows in the dashboard's Ask Claude panel."""
    with db.session() as s:
        sig = s.get(SignalRow, signal_id)
        if sig is None:
            raise KeyError(f"no signal {signal_id}")
        db.log(
            s, "ask", f"Claude Code reviewed {sig.symbol}: {question}", sig.market, signal=sig.id,
            question=question, answer=answer, risks=risks, cost_usd=0.0, source="claude-code",
        )  # fmt: skip
        s.expunge(sig)
    return sig


def recent_reviews(db: Database, limit: int = 20) -> list[dict[str, Any]]:
    with db.session() as s:
        rows = s.scalars(
            select(EventRow).where(EventRow.kind == "ask").order_by(EventRow.ts.desc()).limit(limit)
        ).all()
        return [r.data | {"ts": r.ts.isoformat()} for r in rows]
