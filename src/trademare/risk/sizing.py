"""Position sizing: risk a fixed share of capital per trade, within caps."""

from __future__ import annotations

import math

from trademare.config.schema import MarketConfig


def position_size(
    entry: float,
    stop: float,
    capital: float,
    cash: float,
    cfg: MarketConfig,
    sector_exposure: float = 0.0,
    gross_exposure: float = 0.0,
) -> int:
    """Shares to trade so a stop-out loses ``risk_per_trade_pct`` of ``capital``.

    The result is the smallest of:
    - risk-based size: capital x risk% / |entry - stop|
    - single-position cap: capital x max_position_pct / entry
    - sector cap: remaining room under capital x max_sector_pct
    - deployable cap: capital x (1 - cash_buffer) minus what is already deployed
    - available cash
    """
    risk_per_share = abs(entry - stop)
    if entry <= 0 or risk_per_share <= 0 or capital <= 0:
        return 0
    limits = [
        capital * cfg.risk_per_trade_pct / 100 / risk_per_share,
        capital * cfg.max_position_pct / 100 / entry,
        (capital * cfg.max_sector_pct / 100 - sector_exposure) / entry,
        (capital * (1 - cfg.cash_buffer_pct / 100) - gross_exposure) / entry,
        cash / entry,
    ]
    return max(0, math.floor(min(limits)))
