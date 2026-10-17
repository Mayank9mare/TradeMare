"""Contracts implemented by data providers, analysts and LLM clients.

Broker adapters live in ``trademare.execution.brokers.base``.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

import pandas as pd
from pydantic import BaseModel, Field

from trademare.core.models import Market

BAR_COLUMNS = ("open", "high", "low", "close", "volume")


@runtime_checkable
class PriceProvider(Protocol):
    """Daily OHLCV bars.

    Returns a DataFrame indexed by session date (ascending) with columns
    ``BAR_COLUMNS``, adjusted for splits and dividends.
    """

    def get_daily_bars(self, market: Market, symbol: str, start: date, end: date) -> pd.DataFrame: ...


@runtime_checkable
class FundamentalsProvider(Protocol):
    """Financial statements as a standard frame (see ``trademare.data.fields``).

    Each row carries ``available_from`` so callers can take a point-in-time
    view with ``trademare.data.fields.point_in_time``.
    """

    source: str

    def get_financials(self, market: Market, symbol: str) -> pd.DataFrame: ...


class AnalystResult(BaseModel):
    analyst: str
    score: float = Field(ge=-1, le=1, description="-1 strongly bearish .. +1 strongly bullish")
    confidence: float = Field(ge=0, le=1)
    reasons: list[str] = Field(default_factory=list)
    details: dict[str, float | None] = Field(default_factory=dict, description="Sub-scores / metrics")


@runtime_checkable
class Analyst(Protocol):
    """Scores one symbol using only information available on ``asof``."""

    name: str

    def analyze(self, market: Market, symbol: str, asof: date) -> AnalystResult: ...


@runtime_checkable
class LLMClient(Protocol):
    """Provider-agnostic text completion (Claude, OpenAI, ...)."""

    def complete(self, system: str, prompt: str, max_tokens: int = 1024) -> str: ...
