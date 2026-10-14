"""Local Parquet cache for bars and financials.

Layout under ``root``::

    bars/<market>/<symbol>.parquet
    financials/<market>/<source>/<symbol>.parquet
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import unquote

import pandas as pd

from trademare.core.models import Market

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _filename(symbol: str) -> str:
    # Symbols like "M&M" or "BRK.B" must map to safe, unique filenames.
    return _UNSAFE.sub(lambda m: f"%{ord(m.group()):02X}", symbol) + ".parquet"


class ParquetStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _bars_path(self, market: Market, symbol: str) -> Path:
        return self.root / "bars" / market.value / _filename(symbol)

    def _financials_path(self, market: Market, source: str, symbol: str) -> Path:
        return self.root / "financials" / market.value / source / _filename(symbol)

    @staticmethod
    def _write(df: pd.DataFrame, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        df.to_parquet(tmp)
        tmp.replace(path)  # atomic: a crash never leaves a half-written file

    @staticmethod
    def _read(path: Path) -> pd.DataFrame | None:
        return pd.read_parquet(path) if path.exists() else None

    @staticmethod
    def _mtime(path: Path) -> datetime | None:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC) if path.exists() else None

    # Bars
    def read_bars(self, market: Market, symbol: str) -> pd.DataFrame | None:
        return self._read(self._bars_path(market, symbol))

    def write_bars(self, market: Market, symbol: str, df: pd.DataFrame) -> None:
        self._write(df, self._bars_path(market, symbol))

    def _manifest_path(self, market: Market) -> Path:
        return self.root / "bars" / market.value / "_history_start.json"

    def bars_history_start(self, market: Market, symbol: str) -> date | None:
        """Earliest date requested when the symbol's full history was downloaded.

        Distinguishes "listed recently" from "cache predates a larger history_years".
        """
        path = self._manifest_path(market)
        if not path.exists():
            return None
        value = json.loads(path.read_text()).get(symbol)
        return date.fromisoformat(value) if value else None

    def set_bars_history_start(self, market: Market, symbols: list[str], start: date) -> None:
        path = self._manifest_path(market)
        manifest = json.loads(path.read_text()) if path.exists() else {}
        manifest.update({s: start.isoformat() for s in symbols})
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(manifest, indent=0, sort_keys=True))
        tmp.replace(path)

    # Financials
    def read_financials(self, market: Market, source: str, symbol: str) -> pd.DataFrame | None:
        return self._read(self._financials_path(market, source, symbol))

    def write_financials(self, market: Market, source: str, symbol: str, df: pd.DataFrame) -> None:
        self._write(df, self._financials_path(market, source, symbol))

    def financials_updated_at(self, market: Market, source: str, symbol: str) -> datetime | None:
        return self._mtime(self._financials_path(market, source, symbol))

    def bar_symbols(self, market: Market) -> list[str]:
        folder = self.root / "bars" / market.value
        if not folder.exists():
            return []
        return sorted(unquote(p.stem) for p in folder.glob("*.parquet"))
