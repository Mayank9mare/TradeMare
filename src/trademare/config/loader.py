"""Load settings.yaml, overlaid with an optional git-ignored settings.local.yaml.

The local file holds personal values (real budgets, email address) so they
stay out of the public repo. Its keys are deep-merged over the base file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from trademare.config.schema import AppConfig

DEFAULT_CONFIG_PATH = Path("config/settings.yaml")
CONFIG_ENV_VAR = "TRADEMARE_CONFIG"


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping at the top level")
    return data


def local_override_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.local{path.suffix}")


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load and validate config. Re-reads from disk on every call."""
    path = Path(path or os.environ.get(CONFIG_ENV_VAR) or DEFAULT_CONFIG_PATH)
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")

    data = _read_yaml(path)
    local = local_override_path(path)
    if local.exists():
        data = deep_merge(data, _read_yaml(local))
    return AppConfig.model_validate(data)
