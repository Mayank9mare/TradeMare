"""Run backtests from config and save/load their results under data/backtests/."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from trademare.backtest.engine import Backtester
from trademare.backtest.metrics import summarize, trades_frame
from trademare.config.schema import AppConfig
from trademare.core.models import Market
from trademare.data.service import DataService
from trademare.strategy.scanner import ScanContext


def apply_overrides(config: AppConfig, overrides: list[str]) -> AppConfig:
    """Apply ``dotted.key=value`` overrides (values parsed as YAML), re-validating the config."""
    data = config.model_dump(mode="json")
    for item in overrides:
        key, sep, raw = item.partition("=")
        if not sep:
            raise ValueError(f"override '{item}' must look like key=value")
        node = data
        parts = key.strip().split(".")
        for part in parts[:-1]:
            if part not in node or not isinstance(node[part], dict):
                raise ValueError(f"unknown config key '{key}'")
            node = node[part]
        if parts[-1] not in node:
            raise ValueError(f"unknown config key '{key}'")
        node[parts[-1]] = yaml.safe_load(raw)
    return AppConfig.model_validate(data)


def backtests_dir(config: AppConfig) -> Path:
    return Path(config.data.cache_dir).parent / "backtests"


def run_backtest(
    service: DataService,
    market: Market,
    start: date | None = None,
    end: date | None = None,
    overrides: list[str] | None = None,
    progress=None,
) -> tuple[str, dict]:
    config = apply_overrides(service.config, overrides or [])
    service.config = config  # the scan context reads strategy settings from here
    ctx = ScanContext(service, market)
    end = end or ctx.last_date()
    start = start or end - timedelta(days=round(365.25 * config.backtest.years))
    result = Backtester(ctx, config).run(start, end, progress=progress)
    summary = summarize(result, config.backtest.out_of_sample_fraction)

    # Random suffix: parallel runs started in the same second must not overwrite each other.
    run_id = (
        f"{market.value}-{result.start}-{result.end}-{datetime.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
    )
    folder = backtests_dir(config) / run_id
    folder.mkdir(parents=True, exist_ok=True)
    meta: dict[str, Any] = {
        "id": run_id,
        "market": market.value,
        "start": result.start.isoformat(),
        "end": result.end.isoformat(),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "initial_capital": result.initial_capital,
        "final_equity": float(result.equity.iloc[-1]),
        "overrides": overrides or [],
        "open_positions": len(result.open_positions),
        "settings": {
            "market": config.markets[market].model_dump(mode="json"),
            "strategy": config.strategy.model_dump(mode="json"),
            "backtest": config.backtest.model_dump(mode="json"),
        },
        "summary": summary,
    }
    (folder / "summary.json").write_text(json.dumps(meta, indent=2, default=str))
    curves = pd.DataFrame({"equity": result.equity, "exposure": result.exposure})
    if result.benchmark is not None:
        curves["benchmark"] = result.benchmark
    curves.to_parquet(folder / "curves.parquet")
    trades_frame(result.trades).to_parquet(folder / "trades.parquet")
    return run_id, meta


def list_backtests(config: AppConfig) -> list[dict]:
    root = backtests_dir(config)
    if not root.exists():
        return []
    runs = [json.loads(p.read_text()) for p in root.glob("*/summary.json")]
    return sorted(runs, key=lambda r: r["created_at"], reverse=True)


_RULE_KEYS_MARKET = (
    "universe", "universe_top_n", "strategy_mode", "factors", "min_avg_traded_value", "allow_long",
    "allow_short", "risk_per_trade_pct", "max_position_pct", "max_open_positions", "max_sector_pct",
    "stop_loss", "take_profit", "time_stop_days",
)  # fmt: skip
_RULE_KEYS_STRATEGY = (
    "factor_rebalance", "factor_entry_pct", "factor_exit_pct", "risk_off_exposure", "vol_target",
    "exclude_high_vol_pct", "min_composite_score", "weights", "regime_penalty",
)  # fmt: skip


# The pre-registered studies and what they concluded (each doc holds the plan and its results).
RESEARCH: tuple[dict[str, str], ...] = (
    {"title": "India factor strategy v3 (momentum + earnings yield, volatility controls)",
     "verdict": "pass", "doc": "2026-10-18-strategy-v3-plan.md",
     "note": "Passed holdout and walk-forward; the India paper strategy uses these settings."},
    {"title": "US factor strategy (India v3 recipe on the S&P 500)", "verdict": "fail",
     "doc": "2026-10-19-us-factor-plan.md", "note": "Failed walk-forward; the US stays signals-only."},
    {"title": "India IPOs: institutional demand vs listing gains", "verdict": "pass",
     "doc": "2026-10-19-ipo-demand-plan.md", "note": "Strong link in both halves; used for IPO labels."},
    {"title": "Strategy v2 (factor ranks)", "verdict": "fail", "doc": "2026-10-17-strategy-v2-plan.md",
     "note": "Drawdown too deep out of sample; led to v3."},
    {"title": "Strategy v1 (multi-analyst swing)", "verdict": "fail",
     "doc": "2026-10-17-strategy-v1-baseline.md", "note": "No reliable edge."},
)  # fmt: skip


def matches_current(run: dict, config: AppConfig) -> bool:
    """True when the run used the trading rules the market is configured with now (budget aside)."""
    snap = run.get("settings") or {}
    market = config.markets.get(run.get("market"))
    if market is None or "market" not in snap:
        return False
    now_m = market.model_dump(mode="json")
    now_s = config.strategy.model_dump(mode="json")
    return all(snap["market"].get(k) == now_m.get(k) for k in _RULE_KEYS_MARKET) and all(
        snap.get("strategy", {}).get(k) == now_s.get(k) for k in _RULE_KEYS_STRATEGY
    )


def load_backtest(config: AppConfig, run_id: str) -> tuple[dict, pd.DataFrame, pd.DataFrame] | None:
    folder = backtests_dir(config) / run_id
    if not (folder / "summary.json").exists() or "/" in run_id or ".." in run_id:
        return None
    meta = json.loads((folder / "summary.json").read_text())
    return meta, pd.read_parquet(folder / "curves.parquet"), pd.read_parquet(folder / "trades.parquet")


_PCT_SIGNED = {"total_return", "cagr"}
_PCT = {"volatility", "max_drawdown", "win_rate", "exposure"}


def _fmt(key: str, value: float | None) -> str:
    if value is None:
        return "—"
    if key in _PCT_SIGNED:
        return f"{value:+.1%}"
    if key in _PCT:
        return f"{value:.1%}"
    if key in {"trades", "longest_drawdown_days"}:
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def format_summary(meta: dict) -> str:
    """Plain-text report for the terminal."""
    s, st, bm = meta["summary"], meta["summary"]["strategy"], meta["summary"]["benchmark"]
    lines = [
        f"[{meta['market']}] backtest {meta['start']} .. {meta['end']}  ({meta['id']})",
        f"  capital {meta['initial_capital']:,.0f} -> {meta['final_equity']:,.0f}; "
        f"open positions at end: {meta['open_positions']}",
        f"  {'':<26}{'Strategy':>12}{'Index':>12}",
    ]
    for key, label in (
        ("total_return", "Total return"), ("cagr", "CAGR"), ("volatility", "Volatility"),
        ("sharpe", "Sharpe"), ("sortino", "Sortino"), ("max_drawdown", "Max drawdown"),
        ("calmar", "Calmar"), ("longest_drawdown_days", "Longest drawdown (days)"),
    ):  # fmt: skip
        lines.append(f"  {label:<26}{_fmt(key, st.get(key)):>12}{_fmt(key, bm.get(key)):>12}")
    lines.append(
        f"  trades {_fmt('trades', st.get('trades'))}, win rate {_fmt('win_rate', st.get('win_rate'))}, "
        f"profit factor {_fmt('pf', st.get('profit_factor'))}, avg R {_fmt('r', st.get('avg_r'))}, "
        f"avg hold {_fmt('d', st.get('avg_bars_held'))} sessions, "
        f"exposure {_fmt('exposure', s.get('exposure_avg'))}"
    )
    lines.append(f"  costs paid {st.get('total_costs', 0):,.0f}; exits: {s['exit_reasons']}")
    ks = s.get("kill_switch") or {}
    if ks.get("trips"):
        first = ks["trips"][0]
        lines.append(
            f"  kill switch tripped {len(ks['trips'])}x, {ks['halted_sessions']} sessions without "
            f"new entries (first {first['date']}: {first['reason']})"
        )
    years = "  ".join(
        f"{y}: {_fmt('total_return', r['strategy'])} vs {_fmt('total_return', r.get('benchmark'))}"
        for y, r in s["by_year"].items()
    )
    lines.append(f"  by year: {years}")
    for label in ("in_sample", "out_of_sample"):
        if label not in s:
            continue
        seg_s, seg_b = s[label]["strategy"], s[label]["benchmark"]
        lines.append(
            f"  {label.replace('_', '-'):<14} (split {s['split_date']}): "
            f"CAGR {_fmt('cagr', seg_s.get('cagr'))} vs {_fmt('cagr', seg_b.get('cagr'))}, "
            f"Sharpe {_fmt('sharpe', seg_s.get('sharpe'))} vs {_fmt('sharpe', seg_b.get('sharpe'))}, "
            f"max DD {_fmt('max_drawdown', seg_s.get('max_drawdown'))}, "
            f"trades {_fmt('trades', seg_s.get('trades'))}"
        )
    return "\n".join(lines)
