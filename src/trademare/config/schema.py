"""Typed configuration schema.

Everything tunable (budgets, stop-loss rules, risk limits, weights) is defined
here and loaded from YAML, so behaviour changes never require code changes.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trademare.core.models import Market


class _Strict(BaseModel):
    """Reject unknown keys so typos in settings.yaml fail loudly."""

    model_config = ConfigDict(extra="forbid")


class StopLossMethod(StrEnum):
    ATR = "atr"
    PERCENT = "percent"
    SWING_LOW = "swing_low"
    FIXED_AMOUNT = "fixed_amount"


class TrailMethod(StrEnum):
    ATR = "atr"
    PERCENT = "percent"


class TakeProfitMethod(StrEnum):
    R_MULTIPLE = "r_multiple"
    PERCENT = "percent"
    NONE = "none"


class ShortInstrument(StrEnum):
    CASH = "cash"
    FUTURES = "futures"


class StopLossConfig(_Strict):
    method: StopLossMethod = StopLossMethod.ATR
    atr_multiple: float = Field(2.0, gt=0)
    atr_period: int = Field(14, ge=2)
    percent: float = Field(5.0, gt=0, lt=100)
    fixed_amount: float | None = Field(None, gt=0, description="Per-share stop distance in market currency")
    swing_lookback: int = Field(10, ge=2, description="Bars to look back for the swing low/high")
    trailing: bool = False
    trail_method: TrailMethod = TrailMethod.ATR
    trail_value: float = Field(2.5, gt=0, description="ATR multiple or percent, per trail_method")

    @model_validator(mode="after")
    def _method_has_params(self) -> Self:
        if self.method is StopLossMethod.FIXED_AMOUNT and self.fixed_amount is None:
            raise ValueError("stop_loss.method 'fixed_amount' requires stop_loss.fixed_amount")
        return self


class TakeProfitConfig(_Strict):
    method: TakeProfitMethod = TakeProfitMethod.R_MULTIPLE
    value: float = Field(2.0, gt=0, description="R multiple or percent, per method")


class KillSwitchConfig(_Strict):
    daily_loss_pct: float = Field(3.0, gt=0, le=100)
    max_drawdown_pct: float = Field(10.0, gt=0, le=100)


# Brokers that can trade each market. "paper" is the built-in simulator.
ALLOWED_BROKERS: dict[Market, set[str]] = {
    Market.IN: {"zerodha", "dhan", "paper"},
    Market.US: {"alpaca", "paper"},
}

# Data sources implemented for each market.
ALLOWED_PRICE_SOURCES: dict[Market, set[str]] = {
    Market.IN: {"yfinance"},
    Market.US: {"yfinance"},
}
ALLOWED_FUNDAMENTALS_SOURCES: dict[Market, set[str]] = {
    Market.IN: {"yfinance"},
    Market.US: {"edgar", "yfinance"},
}


class MarketConfig(_Strict):
    enabled: bool = True
    broker: str
    universe: str = Field(description="Name of the symbol universe, e.g. nifty200 or sp500")
    price_source: str = "yfinance"
    fundamentals_source: str = "yfinance"
    budget: float = Field(gt=0, description="Hard cap on deployed capital, in market currency")
    cash_buffer_pct: float = Field(10.0, ge=0, lt=100)

    allow_long: bool = True
    allow_short: bool = False
    short_instrument: ShortInstrument = ShortInstrument.CASH

    risk_per_trade_pct: float = Field(1.0, gt=0, le=10)
    max_position_pct: float = Field(15.0, gt=0, le=100)
    max_open_positions: int = Field(8, ge=1)
    max_sector_pct: float = Field(30.0, gt=0, le=100)

    stop_loss: StopLossConfig = StopLossConfig()
    take_profit: TakeProfitConfig = TakeProfitConfig()
    time_stop_days: int | None = Field(15, ge=1)
    signal_expiry_sessions: int = Field(1, ge=1)
    kill_switch: KillSwitchConfig = KillSwitchConfig()

    @property
    def deployable_capital(self) -> float:
        return self.budget * (1 - self.cash_buffer_pct / 100)

    @model_validator(mode="after")
    def _sane_limits(self) -> Self:
        if not (self.allow_long or self.allow_short):
            raise ValueError("at least one of allow_long / allow_short must be true")
        if self.risk_per_trade_pct > self.max_position_pct:
            raise ValueError("risk_per_trade_pct cannot exceed max_position_pct")
        return self


class StrategyWeights(_Strict):
    fundamental: float = Field(0.35, ge=0)
    technical: float = Field(0.50, ge=0)
    sentiment: float = Field(0.15, ge=0)

    @model_validator(mode="after")
    def _non_zero(self) -> Self:
        if self.fundamental + self.technical + self.sentiment == 0:
            raise ValueError("strategy weights cannot all be zero")
        return self


class StrategyConfig(_Strict):
    weights: StrategyWeights = StrategyWeights()
    min_composite_score: float = Field(0.4, ge=-1, le=1)
    min_fundamental_score: float = Field(0.0, ge=-1, le=1, description="Fundamental gate for entries")


class EmailConfig(_Strict):
    enabled: bool = False
    to: list[str] = []
    send_empty_digest: bool = False


class NotificationsConfig(_Strict):
    email: EmailConfig = EmailConfig()


class LLMProvider(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


class LLMConfig(_Strict):
    enabled: bool = False
    provider: LLMProvider = LLMProvider.ANTHROPIC
    model: str = "claude-sonnet-5-5"
    max_daily_cost_usd: float = Field(1.0, ge=0)


class DataConfig(_Strict):
    cache_dir: str = "data/cache"
    history_years: int = Field(8, ge=1, description="Years of daily bars to keep (backtest + warm-up)")
    fundamentals_refresh_days: int = Field(7, ge=1)
    batch_size: int = Field(50, ge=1, le=500, description="Symbols per bulk price request")


class AppConfig(_Strict):
    database_url: str = "sqlite:///data/trademare.db"
    log_level: str = "INFO"
    data: DataConfig = DataConfig()
    markets: dict[Market, MarketConfig]
    strategy: StrategyConfig = StrategyConfig()
    notifications: NotificationsConfig = NotificationsConfig()
    llm: LLMConfig = LLMConfig()

    @model_validator(mode="after")
    def _market_rules(self) -> Self:
        for market, cfg in self.markets.items():
            allowed = ALLOWED_BROKERS[market]
            if cfg.broker not in allowed:
                raise ValueError(f"markets.{market}.broker '{cfg.broker}' must be one of {sorted(allowed)}")
            for key, table in (
                ("price_source", ALLOWED_PRICE_SOURCES),
                ("fundamentals_source", ALLOWED_FUNDAMENTALS_SOURCES),
            ):
                value = getattr(cfg, key)
                if value not in table[market]:
                    raise ValueError(
                        f"markets.{market}.{key} '{value}' must be one of {sorted(table[market])}"
                    )
            # Indian cash-segment shorts must be squared off the same day, so a
            # multi-day swing short is only possible via stock futures.
            if market is Market.IN and cfg.allow_short and cfg.short_instrument is ShortInstrument.CASH:
                raise ValueError(
                    "markets.IN.allow_short requires short_instrument: futures "
                    "(cash-segment shorts are intraday-only in India)"
                )
        return self

    def enabled_markets(self) -> dict[Market, MarketConfig]:
        return {m: c for m, c in self.markets.items() if c.enabled}
