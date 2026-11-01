"""Settings for the intraday module: config/intraday.yaml overlaid with config/intraday.local.yaml."""

from __future__ import annotations

import re
from datetime import date, time
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_PATH = Path("config/intraday.yaml")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PaperConfig(_Strict):
    capital: float = Field(100_000, gt=0, description="Separate paper capital, INR")
    risk_pct: float = Field(0.5, gt=0, le=5, description="Loss if a trade hits its stop, % of capital")
    max_positions: int = Field(5, ge=1)
    max_position_pct: float = Field(50, gt=0, le=100, description="No leverage: position value cap")
    strategies: list[str] = Field(default_factory=list, description="Strategy ids to paper-trade live")
    require_record: bool = Field(True, description="Only strategies that pass the evidence bar")


class IntradayConfig(_Strict):
    data_dir: str = "data/intraday"
    universe: str = "nifty50"
    interval: str = "5minute"
    history_start: date = date(2018, 1, 1)
    entry_start: time = time(9, 20)
    entry_end: time = time(14, 45)
    squareoff: time = time(15, 15)
    stop_valid_candles: int = Field(3, ge=1)
    slippage_bps: float = Field(2.0, ge=0)
    cost_notional: float = Field(50_000, gt=0, description="Position size used for the cost estimate")
    port: int = 8766
    paper: PaperConfig = PaperConfig()

    @field_validator("interval")
    @classmethod
    def _interval(cls, v: str) -> str:
        if not re.fullmatch(r"(\d+)?minute", v):
            raise ValueError("interval must be a Kite minute interval such as 5minute")
        return v

    @property
    def minutes(self) -> int:
        return int(self.interval.removesuffix("minute") or 1)

    @property
    def root(self) -> Path:
        return Path(self.data_dir)


def _merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def local_path(path: Path) -> Path:
    return path.with_name(path.stem + ".local" + path.suffix)


def load(path: str | Path | None = None) -> IntradayConfig:
    path = Path(path or DEFAULT_PATH)
    base = (yaml.safe_load(path.read_text()) or {}) if path.exists() else {}
    local = local_path(path)
    if local.exists():
        base = _merge(base, yaml.safe_load(local.read_text()) or {})
    return IntradayConfig.model_validate(base)


def save_local(changes: dict, path: str | Path | None = None) -> IntradayConfig:
    """Merge ``changes`` into config/intraday.local.yaml after validating the result."""
    path = Path(path or DEFAULT_PATH)
    local = local_path(path)
    current = (yaml.safe_load(local.read_text()) or {}) if local.exists() else {}
    merged = _merge(current, changes)
    base = (yaml.safe_load(path.read_text()) or {}) if path.exists() else {}
    config = IntradayConfig.model_validate(_merge(base, merged))
    local.write_text(
        "# Personal intraday overrides (git-ignored).\n" + yaml.safe_dump(merged, sort_keys=False)
    )
    return config
