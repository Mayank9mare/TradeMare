"""The intraday universe list, kept in the module's own data folder (never the daily system's)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def path(root: Path, name: str) -> Path:
    return Path(root) / f"{name}.csv"


def refresh(root: Path, name: str) -> pd.DataFrame:
    from trademare.data.universe import refresh_universe  # read-only use: writes only into ``root``

    return refresh_universe(name, directory=Path(root))


def load(root: Path, name: str) -> pd.DataFrame:
    p = path(root, name)
    if not p.exists():
        raise FileNotFoundError(f"no {name} list yet: run `trademare-intraday universe`")
    return pd.read_csv(p)
