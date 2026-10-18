"""Editable settings for the dashboard.

Changes are validated against the full config schema, then written only to
``settings.local.yaml`` (merged with what is already there). Safety-critical
keys — ``validated`` and ``broker`` — are deliberately not editable here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from trademare.config.loader import DEFAULT_CONFIG_PATH, deep_merge, local_override_path
from trademare.config.schema import AppConfig


@dataclass(frozen=True)
class FieldSpec:
    key: str  # dotted path inside markets.<M>, or a top-level path for global fields
    label: str
    kind: str  # float | int | bool | choice | optional_int | emails
    choices: tuple[str, ...] = ()
    help: str = ""


MARKET_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("enabled", "Market enabled", "bool"),
    FieldSpec("budget", "Budget", "float", help="Hard cap on capital used, in the market's currency"),
    FieldSpec("cash_buffer_pct", "Cash buffer %", "float"),
    FieldSpec("risk_per_trade_pct", "Risk per trade %", "float", help="Loss if a trade hits its stop"),
    FieldSpec("max_position_pct", "Max position %", "float"),
    FieldSpec("max_sector_pct", "Max sector %", "float"),
    FieldSpec("max_open_positions", "Max open positions", "int"),
    FieldSpec(
        "stop_loss.method", "Stop-loss method", "choice", ("atr", "percent", "swing_low", "fixed_amount")
    ),
    FieldSpec("stop_loss.atr_multiple", "Stop: ATR multiple", "float"),
    FieldSpec("stop_loss.percent", "Stop: percent", "float"),
    FieldSpec("stop_loss.trailing", "Trailing stop", "bool"),
    FieldSpec(
        "stop_loss.limit_buffer_pct",
        "Stop limit buffer %",
        "float",
        help="How far past the trigger a broker limit stop is priced; bigger gaps exit at the next open",
    ),
    FieldSpec("take_profit.method", "Take-profit method", "choice", ("r_multiple", "percent", "none")),
    FieldSpec("take_profit.value", "Take-profit value", "float", help="R multiple or percent"),
    FieldSpec("time_stop_days", "Time stop (sessions, blank = off)", "optional_int"),
    FieldSpec("kill_switch.daily_loss_pct", "Kill switch: daily loss %", "float"),
    FieldSpec("kill_switch.max_drawdown_pct", "Kill switch: drawdown %", "float"),
)

GLOBAL_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("notifications.email.enabled", "Email digest enabled", "bool"),
    FieldSpec("notifications.email.to", "Email recipients (comma-separated)", "emails"),
    FieldSpec("strategy.risk_off_exposure", "Risk-off exposure (share of slots)", "float"),
    FieldSpec(
        "strategy.vol_target",
        "Volatility targeting",
        "bool",
        help="Fewer positions when the index is more volatile than usual",
    ),
    FieldSpec(
        "strategy.exclude_high_vol_pct",
        "Exclude most volatile share",
        "float",
        help="No new entries in this most-volatile share of stocks (0.2 = top 20%)",
    ),
)


def _get(data: dict[str, Any], dotted: str) -> Any:
    for part in dotted.split("."):
        data = data[part]
    return data


def _set(data: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    for part in parts[:-1]:
        data = data.setdefault(part, {})
    data[parts[-1]] = value


def current_values(config: AppConfig) -> dict[str, Any]:
    """Form field name -> current value (``IN.budget``, ``notifications.email.to``, ...)."""
    data = config.model_dump(mode="json")
    values: dict[str, Any] = {}
    for market in data["markets"]:
        for f in MARKET_FIELDS:
            values[f"{market}.{f.key}"] = _get(data["markets"][market], f.key)
    for f in GLOBAL_FIELDS:
        values[f.key] = _get(data, f.key)
    return values


def _parse(spec: FieldSpec, raw: str | None) -> Any:
    raw = (raw or "").strip()
    if spec.kind == "bool":
        return raw in ("on", "true", "1")
    if spec.kind == "int":
        return int(raw)
    if spec.kind == "optional_int":
        return int(raw) if raw else None
    if spec.kind == "float":
        return float(raw)
    if spec.kind == "emails":
        return [e.strip() for e in raw.split(",") if e.strip()]
    if spec.kind == "choice":
        if raw not in spec.choices:
            raise ValueError(f"must be one of {', '.join(spec.choices)}")
        return raw
    raise ValueError(f"unknown field kind {spec.kind}")


def apply_form(form: dict[str, str], config_path: Path | None = None) -> tuple[dict[str, Any], list[str]]:
    """Validate a submitted form and save the changes to settings.local.yaml.

    Returns (changes written, errors). Nothing is written when there are errors.
    """
    config_path = Path(config_path or DEFAULT_CONFIG_PATH)
    local_path = local_override_path(config_path)
    base = yaml.safe_load(config_path.read_text()) or {}
    local = (yaml.safe_load(local_path.read_text()) or {}) if local_path.exists() else {}
    current = current_values(AppConfig.model_validate(deep_merge(base, local)))

    changes: dict[str, Any] = {}
    errors: list[str] = []
    specs = [(f"{m}.{f.key}", f, m) for m in base.get("markets", {}) for f in MARKET_FIELDS]
    specs += [(f.key, f, None) for f in GLOBAL_FIELDS]
    for name, spec, market in specs:
        try:
            value = _parse(spec, form.get(name))
        except ValueError as e:
            errors.append(f"{name}: {e}")
            continue
        if value != current.get(name):
            _set(changes, f"markets.{market}.{spec.key}" if market else spec.key, value)
    if errors or not changes:
        return {}, errors

    merged_local = deep_merge(local, changes)
    try:
        AppConfig.model_validate(deep_merge(base, merged_local))
    except ValidationError as e:
        return {}, [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()]
    header = "# Personal overrides (git-ignored). Edited by the TradeMare dashboard; hand edits are kept.\n"
    local_path.write_text(header + yaml.safe_dump(merged_local, sort_keys=False, allow_unicode=True))
    return changes, []
