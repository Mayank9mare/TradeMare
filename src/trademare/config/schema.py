"""Typed configuration schema.

Everything tunable (budgets, stop-loss rules, risk limits, weights) is defined
here and loaded from YAML, so behaviour changes never require code changes.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

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


class StrategyMode(StrEnum):
    SWING = "swing"  # v1: multi-analyst composite, daily ideas
    FACTOR = "factor"  # v2: cross-sectional factor ranks, periodic rebalance


class Rebalance(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


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
    limit_buffer_pct: float = Field(
        1.0,
        ge=0.1,
        le=10,
        description="Stop orders that must be limit orders (Zerodha GTT, Dhan Forever) are priced this far "
        "through the trigger; a gap beyond it is handled as a stuck stop (market exit next open)",
    )

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
    Market.IN: {"zerodha", "dhan", "groww", "paper"},
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


class TaxConfig(_Strict):
    """Capital-gains timing hint on exit signals (information only, never blocks a trade)."""

    warn_within_days: int = Field(
        60, ge=0, le=365, description="Hint when a profitable holding turns long-term within this many days"
    )
    short_term_rate_pct: float | None = Field(None, ge=0, le=100, description="For the saving estimate")
    long_term_rate_pct: float | None = Field(None, ge=0, le=100, description="For the saving estimate")


class PreTradeConfig(_Strict):
    """Last checks before an order reaches a live broker (paper trading logs them as warnings)."""

    enabled: bool = True
    price_band_pct: float = Field(
        5.0, gt=0, le=50, description="A buy's limit must be within this % of the last close"
    )
    max_orders_per_day: int = Field(10, ge=1, description="New buy orders allowed per day")
    check_funds: bool = Field(True, description="Live: refuse a buy the broker account cannot pay for")
    check_surveillance: bool = Field(
        True, description="India: no new buys in stocks on NSE's ASM/GSM surveillance lists"
    )


class MarketConfig(_Strict):
    enabled: bool = True
    validated: bool = Field(
        False,
        description="Set true only after a strategy passes its out-of-sample bar; live orders require it",
    )
    broker: str
    universe: str = Field(description="Name of the symbol universe, e.g. nifty200 or sp500")
    price_source: str = "yfinance"
    fundamentals_source: str = "yfinance"
    benchmark: str | None = Field(
        None, description="Index symbol for relative strength and regime, e.g. ^NSEI"
    )
    universe_top_n: int | None = Field(
        None,
        ge=10,
        description="If set, on each date trade only the N most-traded names (6-month average value) of the "
        "universe — approximates point-in-time index membership and reduces survivorship bias",
    )
    strategy_mode: StrategyMode = StrategyMode.SWING
    factors: list[str] = Field(default_factory=list, description="Factor names for strategy_mode: factor")
    daily_run_time: str = Field(
        "17:00",
        pattern=r"^\d{2}:\d{2}$",
        description="Local market time (HH:MM) for the daily job, after close",
    )
    min_avg_traded_value: float = Field(
        0.0, ge=0, description="Skip symbols whose 20-day average traded value is below this"
    )
    slippage_bps: float = Field(5.0, ge=0, description="Assumed adverse slippage per market/stop fill")
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
    tax: TaxConfig = TaxConfig()
    pretrade: PreTradeConfig = PreTradeConfig()

    @property
    def deployable_capital(self) -> float:
        return self.budget * (1 - self.cash_buffer_pct / 100)

    @model_validator(mode="after")
    def _sane_limits(self) -> Self:
        if self.strategy_mode is StrategyMode.FACTOR and not self.factors:
            raise ValueError("strategy_mode 'factor' requires a non-empty factors list")
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
    min_composite_score: float = Field(0.4, ge=0, le=1, description="|composite| needed for an idea")
    min_fundamental_score: float = Field(
        0.0, ge=0, le=1, description="Longs need fundamental >= this; shorts need <= -this"
    )
    require_fundamentals: bool = Field(True, description="Skip symbols without fundamental data")
    regime_penalty: float = Field(
        0.15, ge=0, le=1, description="Extra score needed when trading against the market regime"
    )
    max_ideas: int = Field(10, ge=1, description="Per direction, per market")
    avoid_extended_entries: bool = Field(
        True, description="Skip entries whose technical setup argues against the direction (don't chase)"
    )
    exit_score: float = Field(
        0.0, ge=-1, le=1, description="Exit a long when its composite falls to this (short: rises to -this)"
    )
    fundamentals_refresh_days: int = Field(
        7, ge=1, description="How often fundamental scores are recomputed (prices still update daily)"
    )
    # Factor mode (strategy v2)
    factor_rebalance: Rebalance = Rebalance.MONTHLY
    factor_entry_pct: float = Field(0.10, gt=0, le=0.5, description="Buy names ranked in this top share")
    factor_exit_pct: float = Field(0.30, gt=0, le=1, description="Sell names that fall out of this top share")
    risk_off_exposure: float = Field(
        1.0, ge=0, le=1, description="Share of max_open_positions allowed while the regime is risk-off"
    )
    # Strategy v3 risk controls (docs/research/2026-10-18-strategy-v3-plan.md)
    vol_target: bool = Field(
        False,
        description="Scale slots by min(1, typical / current index volatility): 63-session realised vol "
        "vs its trailing 3-year median (Barroso & Santa-Clara 2015)",
    )
    exclude_high_vol_pct: float = Field(
        0.0,
        ge=0,
        lt=1,
        description="Factor mode: no new entries in this most-volatile share of the liquid universe "
        "(252-session volatility); 0 = off",
    )

    @model_validator(mode="after")
    def _factor_bands(self) -> Self:
        if self.factor_exit_pct < self.factor_entry_pct:
            raise ValueError("factor_exit_pct must be >= factor_entry_pct (exit band wider than entry band)")
        return self


class EmailConfig(_Strict):
    enabled: bool = False
    to: list[str] = []
    send_empty_digest: bool = False


class TelegramConfig(_Strict):
    enabled: bool = False
    chat_id: str | None = Field(None, description="Your chat id (run: trademare notify telegram-setup)")
    send_empty: bool = Field(False, description="Also message on days with no signals or alerts")


class WhatsAppConfig(_Strict):
    """Alerts on WhatsApp via Meta's Cloud API (setup: docs/whatsapp-setup.md)."""

    enabled: bool = False
    to: list[str] = Field(
        default_factory=list, description="Your WhatsApp number(s) with country code, e.g. 919812345678"
    )
    phone_number_id: str | None = Field(
        None, description="The sending number's id from Meta's WhatsApp > API Setup page"
    )
    template: str = Field("trademare_alert", description="Approved utility template with one {{1}} parameter")
    language: str = Field("en", description="The template's language code")
    mode: Literal["template", "text"] = Field(
        "template", description="text only reaches you within 24 hours of your last message to the number"
    )
    api_version: str = "v21.0"
    send_empty: bool = Field(False, description="Also message on days with no signals or alerts")


class NotificationsConfig(_Strict):
    email: EmailConfig = EmailConfig()
    telegram: TelegramConfig = TelegramConfig()
    whatsapp: WhatsAppConfig = WhatsAppConfig()
    dashboard_url: str = Field("http://127.0.0.1:8000", description="Used for links in emails and messages")


class LLMProvider(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


class LLMConfig(_Strict):
    """Optional news-sentiment overlay (step 8). Off by default."""

    enabled: bool = False
    provider: LLMProvider = LLMProvider.ANTHROPIC
    model: str = "claude-opus-5-5"
    max_daily_cost_usd: float = Field(1.0, ge=0, description="Stop calling the LLM for the day beyond this")
    lookback_days: int = Field(7, ge=1, le=30, description="Headline window")
    max_headlines: int = Field(15, ge=1, le=50)
    veto_score: float = Field(
        -0.5,
        ge=-1,
        le=0,
        description="Veto a long entry at or below this score (shorts: at or above its negative)",
    )
    input_usd_per_mtok: float | None = Field(None, ge=0, description="Price override; required for openai")
    output_usd_per_mtok: float | None = Field(None, ge=0, description="Price override; required for openai")

    @model_validator(mode="after")
    def _provider_model(self) -> Self:
        if self.provider is LLMProvider.OPENAI:
            if self.model.startswith("claude"):
                raise ValueError("llm.model must name an OpenAI model when llm.provider is openai")
            if self.input_usd_per_mtok is None or self.output_usd_per_mtok is None:
                raise ValueError(
                    "llm.provider openai needs input_usd_per_mtok and output_usd_per_mtok for the cost cap"
                )
        return self


class DataConfig(_Strict):
    cache_dir: str = "data/cache"
    history_years: int = Field(8, ge=1, description="Years of daily bars to keep (backtest + warm-up)")
    fundamentals_refresh_days: int = Field(7, ge=1)
    fundamentals_workers: int = Field(
        2, ge=1, le=16, description="Parallel fundamentals downloads (Yahoo rate-limits)"
    )
    batch_size: int = Field(50, ge=1, le=500, description="Symbols per bulk price request")


class BacktestConfig(_Strict):
    years: float = Field(5, gt=0, description="Default test length ending at the latest cached session")
    out_of_sample_fraction: float = Field(
        0.4, ge=0, lt=1, description="Trailing share of the period reported separately as out-of-sample"
    )
    compound: bool = Field(True, description="Size from current equity (True) or the fixed budget (False)")
    short_borrow_rate_pct: float = Field(0.5, ge=0, description="Annual stock-borrow fee for shorts")
    simulate_kill_switch: bool = Field(True, description="Apply the markets' kill switch in backtests")
    kill_switch_rearm_sessions: int = Field(
        20, ge=1, description="Backtests re-arm a tripped kill switch after this many sessions (live: manual)"
    )


class EventAction(StrEnum):
    WARN = "warn"
    SKIP = "skip"


class EventsConfig(_Strict):
    """Corporate events: official filings (NSE / SEC 8-K) and upcoming results dates."""

    enabled: bool = True
    results_window_sessions: int = Field(
        5, ge=0, le=30, description="Warn about entries whose results are due within this many sessions"
    )
    results_action: EventAction = Field(
        EventAction.WARN, description="warn: flag the signal; skip: no entry signal before results"
    )
    red_flag_action: EventAction = Field(
        EventAction.WARN, description="warn: flag the signal; skip: no entry signal with a red flag"
    )


class OpsConfig(_Strict):
    """Keeping the system itself healthy: database backups and a missed-run check."""

    backup_enabled: bool = True
    backup_dir: str = "data/backups"
    backup_keep: int = Field(14, ge=1, description="Daily database backups to keep")
    backup_time: str = Field(
        "23:30", pattern=r"^\d{2}:\d{2}$", description="Local time of the nightly backup (HH:MM)"
    )
    health_check: bool = Field(True, description="Alert when a daily run is overdue or backups are stale")
    health_grace_hours: float = Field(
        3, gt=0, le=24, description="Hours after daily_run_time before a missing run counts as missed"
    )
    intraday_check_minutes: int = Field(
        15,
        ge=0,
        le=120,
        description="Check open positions against their stops this often in market hours (0 = off)",
    )
    ipo_alerts: bool = Field(
        True, description="Alert when an Indian mainboard IPO opens soon and on its last afternoon of bidding"
    )


class AppConfig(_Strict):
    database_url: str = "sqlite:///data/trademare.db"
    log_level: str = "INFO"
    data: DataConfig = DataConfig()
    markets: dict[Market, MarketConfig]
    strategy: StrategyConfig = StrategyConfig()
    notifications: NotificationsConfig = NotificationsConfig()
    llm: LLMConfig = LLMConfig()
    events: EventsConfig = EventsConfig()
    ops: OpsConfig = OpsConfig()
    backtest: BacktestConfig = BacktestConfig()

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
