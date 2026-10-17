"""Risk & budget manager for live/paper trading (phase 4).

- Sizing: risk-based, capped by position/sector/cash-buffer limits, and by
  the configured budget — capital is ``min(equity, budget)`` so gains are
  never automatically re-risked beyond what you allotted.
- Slots: ``max_open_positions``, reduced in risk-off regimes.
- Kill switch: trips on a daily loss or drawdown beyond the configured
  limits; new entries stay blocked until it is re-armed manually.
"""

from __future__ import annotations

from dataclasses import dataclass

from trademare.analysis.regime import RegimeLabel
from trademare.config.schema import MarketConfig, StrategyConfig
from trademare.risk.sizing import position_size


@dataclass(frozen=True)
class Exposure:
    gross: float  # sum of |position value| incl. approved-but-unfilled entries
    by_sector: dict[str, float]
    positions: int  # open + pending entries


@dataclass(frozen=True)
class KillCheck:
    tripped: bool
    reason: str | None


class RiskManager:
    def __init__(self, cfg: MarketConfig, strategy: StrategyConfig) -> None:
        self.cfg = cfg
        self.strategy = strategy

    def capital(self, equity: float) -> float:
        return min(equity, self.cfg.budget)

    def max_positions(self, regime: RegimeLabel) -> int:
        if regime is RegimeLabel.RISK_OFF:
            return int(self.cfg.max_open_positions * self.strategy.risk_off_exposure)
        return self.cfg.max_open_positions

    def size(
        self, entry: float, stop: float, sector: str | None, equity: float, cash: float, exposure: Exposure
    ) -> int:
        return position_size(
            entry,
            stop,
            self.capital(equity),
            cash,
            self.cfg,
            sector_exposure=exposure.by_sector.get(sector or "", 0.0),
            gross_exposure=exposure.gross,
        )

    def check_kill_switch(self, equity: float, previous_equity: float | None, peak: float) -> KillCheck:
        ks = self.cfg.kill_switch
        if previous_equity and previous_equity > 0:
            day = equity / previous_equity - 1
            if day <= -ks.daily_loss_pct / 100:
                return KillCheck(True, f"daily loss {day:.1%} breached -{ks.daily_loss_pct}%")
        if peak > 0:
            drawdown = equity / peak - 1
            if drawdown <= -ks.max_drawdown_pct / 100:
                return KillCheck(True, f"drawdown {drawdown:.1%} from peak breached -{ks.max_drawdown_pct}%")
        return KillCheck(False, None)
