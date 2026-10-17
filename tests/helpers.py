"""Synthetic market data for analyst and scanner tests."""

import numpy as np
import pandas as pd

from trademare.data.quality import clean_bars


def make_bars(
    n: int = 400,
    drift: float = 0.0,
    vol: float = 0.01,
    start: str = "2023-01-02",
    price: float = 100.0,
    volume: float = 1e6,
    seed: int = 0,
) -> pd.DataFrame:
    """Geometric random walk with OHLC around each close."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n)
    rets = drift + vol * rng.standard_normal(n)
    close = price * np.exp(np.cumsum(rets))
    open_ = close * (1 - rets / 2)
    high = np.maximum(open_, close) * (1 + vol / 2)
    low = np.minimum(open_, close) * (1 - vol / 2)
    vols = volume * (1 + 0.1 * rng.standard_normal(n)).clip(0.5)
    return clean_bars(
        pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vols}, index=idx)
    )
