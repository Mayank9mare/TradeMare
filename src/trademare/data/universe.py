"""Symbol universes (index constituents) with sector data.

Snapshots live in ``config/universes/<name>.csv`` (columns: symbol, name,
sector, cik) and are refreshed from the source with ``trademare universe refresh``.

Note: a current constituent list applied to past dates introduces
survivorship bias (delisted/removed companies are missing). Treat backtest
results accordingly.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import requests

from trademare.core.models import Market

UNIVERSE_DIR = Path("config/universes")
UNIVERSE_COLUMNS = ["symbol", "name", "sector", "cik"]
_BROWSER_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) TradeMare"}


@dataclass(frozen=True)
class UniverseSource:
    market: Market
    url: str
    parse: Callable[[str], pd.DataFrame]


def _parse_nifty(text: str) -> pd.DataFrame:
    raw = pd.read_csv(io.StringIO(text))
    raw = raw[raw["Series"].str.strip() == "EQ"]
    return pd.DataFrame(
        {
            "symbol": raw["Symbol"].str.strip(),
            "name": raw["Company Name"].str.strip(),
            "sector": raw["Industry"].str.strip(),
            "cik": None,
        }
    )


def _parse_sp500(text: str) -> pd.DataFrame:
    raw = pd.read_csv(io.StringIO(text), dtype={"CIK": str})
    return pd.DataFrame(
        {
            "symbol": raw["Symbol"].str.strip(),
            "name": raw["Security"].str.strip(),
            "sector": raw["GICS Sector"].str.strip(),
            "cik": raw["CIK"].str.strip().str.zfill(10),
        }
    )


def _nifty(slug: str) -> UniverseSource:
    return UniverseSource(
        Market.IN, f"https://www.niftyindices.com/IndexConstituent/ind_{slug}list.csv", _parse_nifty
    )


SOURCES: dict[str, UniverseSource] = {
    "nifty50": _nifty("nifty50"),
    "nifty100": _nifty("nifty100"),
    "nifty200": _nifty("nifty200"),
    "nifty500": _nifty("nifty500"),
    "sp500": UniverseSource(
        Market.US,
        "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv",
        _parse_sp500,
    ),
}


def universe_path(name: str, directory: Path = UNIVERSE_DIR) -> Path:
    return directory / f"{name}.csv"


def load_universe(name: str, directory: Path = UNIVERSE_DIR) -> pd.DataFrame:
    path = universe_path(name, directory)
    if not path.exists():
        raise FileNotFoundError(
            f"universe '{name}' not found at {path}; run: trademare universe refresh {name}"
        )
    df = pd.read_csv(path, dtype={"cik": str})
    return df.reindex(columns=UNIVERSE_COLUMNS)


def refresh_universe(name: str, directory: Path = UNIVERSE_DIR, timeout: float = 30) -> pd.DataFrame:
    try:
        source = SOURCES[name]
    except KeyError:
        raise ValueError(f"unknown universe '{name}' (known: {sorted(SOURCES)})") from None
    resp = requests.get(source.url, headers=_BROWSER_UA, timeout=timeout)
    resp.raise_for_status()
    df = source.parse(resp.text).drop_duplicates("symbol").sort_values("symbol")
    if df.empty:
        raise ValueError(f"universe '{name}' source returned no symbols")
    path = universe_path(name, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return df.reset_index(drop=True)
