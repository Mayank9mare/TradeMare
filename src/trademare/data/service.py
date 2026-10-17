"""Wires config + secrets into the data providers and caches for each market."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from trademare.config.schema import AppConfig
from trademare.config.secrets import Secrets
from trademare.core.clock import MarketClock
from trademare.core.models import Market
from trademare.data.cache import FinancialsCache, FinancialsSource, PriceCache, UpdateReport
from trademare.data.providers.edgar import EdgarFundamentalsProvider, SecClient
from trademare.data.providers.yfinance_provider import YFinanceFundamentalsProvider, YFinancePriceProvider
from trademare.data.store import ParquetStore
from trademare.data.universe import load_universe

log = logging.getLogger(__name__)


@dataclass
class MarketData:
    market: Market
    prices: PriceCache
    financials: FinancialsCache


class DataService:
    def __init__(self, config: AppConfig, secrets: Secrets) -> None:
        self.config = config
        self.store = ParquetStore(config.data.cache_dir)
        self._markets: dict[Market, MarketData] = {}
        self._secrets = secrets

    def market(self, market: Market) -> MarketData:
        if market not in self._markets:
            cfg = self.config.markets[market]
            prices = PriceCache(YFinancePriceProvider(), self.store)  # only source so far
            financials = FinancialsCache(
                self._fundamentals_source(market, cfg.fundamentals_source), self.store
            )
            self._markets[market] = MarketData(market, prices, financials)
        return self._markets[market]

    def _fundamentals_source(self, market: Market, name: str) -> FinancialsSource:
        if name == "edgar":
            ua = self._secrets.sec_user_agent
            if ua:
                ciks = {}
                try:
                    universe = load_universe(self.config.markets[market].universe)
                    with_cik = universe.dropna(subset=["cik"])
                    ciks = dict(zip(with_cik["symbol"], with_cik["cik"], strict=True))
                except FileNotFoundError:
                    pass
                return EdgarFundamentalsProvider(SecClient(ua), ciks)
            log.warning("SEC_USER_AGENT not set in .env; using yfinance for %s fundamentals", market)
        return YFinanceFundamentalsProvider()

    def universe_symbols(self, market: Market) -> list[str]:
        return load_universe(self.config.markets[market].universe)["symbol"].tolist()

    def update_prices(self, market: Market, symbols: list[str], through: date | None = None) -> UpdateReport:
        through = through or MarketClock(market).last_completed_session()
        benchmark = self.config.markets[market].benchmark
        if benchmark and benchmark not in symbols:
            symbols = [*symbols, benchmark]
        log.info("%s: updating prices for %d symbols through %s", market, len(symbols), through)
        return self.market(market).prices.update(
            market, symbols, through, self.config.data.history_years, self.config.data.batch_size
        )

    def update_fundamentals(self, market: Market, symbols: list[str], force: bool = False) -> UpdateReport:
        source = self.market(market).financials.source.source
        log.info("%s: updating %s fundamentals for %d symbols", market, source, len(symbols))
        return self.market(market).financials.update(
            market, symbols, self.config.data.fundamentals_refresh_days, force=force
        )
