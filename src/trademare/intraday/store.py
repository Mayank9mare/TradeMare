"""Candle cache: one Parquet file per symbol under <data_dir>/bars/<interval>/ (IST-naive times)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

COLUMNS = ["open", "high", "low", "close", "volume"]


class CandleStore:
    def __init__(self, root: Path, interval: str) -> None:
        self.dir = Path(root) / "bars" / interval

    def path(self, symbol: str) -> Path:
        return self.dir / f"{symbol.replace('/', '_')}.parquet"

    def read(self, symbol: str) -> pd.DataFrame | None:
        p = self.path(symbol)
        if not p.exists():
            return None
        df = pd.read_parquet(p)
        df.index = pd.DatetimeIndex(df.index, name="time")
        return df

    def write(self, symbol: str, candles: pd.DataFrame) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        candles[COLUMNS].to_parquet(self.path(symbol))

    def merge(self, symbol: str, new: pd.DataFrame) -> pd.DataFrame:
        """Add candles, newer values winning for the same timestamp; returns the full history."""
        old = self.read(symbol)
        combined = new if old is None else pd.concat([old, new])
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        self.write(symbol, combined)
        return combined

    def symbols(self) -> list[str]:
        return sorted(p.stem for p in self.dir.glob("*.parquet")) if self.dir.exists() else []
