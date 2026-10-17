"""Daily scan: score every symbol in a market's universe and propose trade ideas.

Uses cached data only and nothing published after ``asof``, so the same code
drives live scans and (in phase 3) backtests.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, Field

from trademare.analysis.fundamental import FundamentalScore, score_universe
from trademare.analysis.ratios import key_ratios
from trademare.analysis.regime import Regime, RegimeLabel, compute_regime
from trademare.analysis.technical import TechnicalScore, score_features, technical_features
from trademare.config.schema import AppConfig, MarketConfig
from trademare.core.interfaces import AnalystResult
from trademare.core.models import Direction, Market
from trademare.data.service import DataService
from trademare.data.universe import load_universe
from trademare.risk.levels import Levels, compute_levels

log = logging.getLogger(__name__)


class SymbolScore(BaseModel):
    symbol: str
    name: str
    sector: str
    close: float | None
    composite: float | None
    confidence: float
    liquid: bool
    analysts: dict[str, AnalystResult] = Field(default_factory=dict)


class Idea(BaseModel):
    symbol: str
    name: str
    sector: str
    direction: Direction
    composite: float
    confidence: float
    levels: Levels
    reasons: dict[str, list[str]]

    @property
    def rank_key(self) -> float:
        return abs(self.composite) * self.confidence


class ScanResult(BaseModel):
    market: Market
    asof: date
    created_at: datetime = Field(default_factory=datetime.now)
    regime: Regime
    weights: dict[str, float]
    ideas: list[Idea]
    scores: list[SymbolScore]

    def ideas_for(self, direction: Direction) -> list[Idea]:
        return [i for i in self.ideas if i.direction is direction]


def _tech_result(t: TechnicalScore) -> AnalystResult:
    return AnalystResult(
        analyst="technical",
        score=round(t.score, 4),
        confidence=round(t.confidence, 4),
        reasons=t.reasons,
        details={k: round(v, 4) for k, v in t.components.items()},
    )


def _fund_result(f: FundamentalScore) -> AnalystResult:
    return AnalystResult(
        analyst="fundamental",
        score=round(f.score, 4),
        confidence=round(f.confidence, 4),
        reasons=f.reasons,
        details={k: (None if v is None else round(v, 4)) for k, v in f.factors.items()},
    )


def combine(results: dict[str, AnalystResult], weights: dict[str, float]) -> tuple[float | None, float]:
    """Weighted composite over the analysts present; weights are renormalised."""
    present = {k: r for k, r in results.items() if weights.get(k, 0) > 0}
    if not present:
        return None, 0.0
    total = sum(weights[k] for k in present)
    composite = sum(weights[k] * r.score for k, r in present.items()) / total
    confidence = sum(weights[k] * r.confidence for k, r in present.items()) / total
    return composite, confidence


def decide(
    score: SymbolScore,
    cfg: MarketConfig,
    config: AppConfig,
    regime: Regime,
) -> Direction | None:
    """Apply the strategy rules to one scored symbol."""
    s = config.strategy
    tech = score.analysts.get("technical")
    fund = score.analysts.get("fundamental")
    if score.composite is None or tech is None or not score.liquid:
        return None
    if s.require_fundamentals and fund is None:
        return None
    # setup < 0 means "extended / poor long entry"; > 0 means "oversold / poor short entry".
    setup = tech.details.get("setup") or 0.0
    long_entry_ok = not s.avoid_extended_entries or setup >= 0
    short_entry_ok = not s.avoid_extended_entries or setup <= 0
    long_threshold = s.min_composite_score + (s.regime_penalty if regime.label is RegimeLabel.RISK_OFF else 0)
    short_threshold = s.min_composite_score + (s.regime_penalty if regime.label is RegimeLabel.RISK_ON else 0)
    if (
        cfg.allow_long
        and long_entry_ok
        and score.composite >= long_threshold
        and tech.score > 0
        and (fund is None or fund.score >= s.min_fundamental_score)
    ):
        return Direction.LONG
    if (
        cfg.allow_short
        and short_entry_ok
        and score.composite <= -short_threshold
        and tech.score < 0
        and (fund is None or fund.score <= -s.min_fundamental_score)
    ):
        return Direction.SHORT
    return None


def run_scan(service: DataService, market: Market, asof: date | None = None) -> ScanResult:
    config = service.config
    cfg = config.markets[market]
    universe = load_universe(cfg.universe)
    store = service.store
    financials = service.market(market).financials

    benchmark_bars = store.read_bars(market, cfg.benchmark) if cfg.benchmark else None
    if asof is None:
        last_dates = [
            b.index[-1] for s in universe["symbol"] if (b := store.read_bars(market, s)) is not None
        ]
        if not last_dates:
            raise ValueError(f"no cached bars for {market}; run: trademare data update -m {market}")
        asof = max(last_dates).date()
    cutoff = pd.Timestamp(asof)
    if benchmark_bars is not None:
        benchmark_bars = benchmark_bars.loc[:cutoff]

    weights = config.strategy.weights.model_dump()
    if not config.llm.enabled:
        weights["sentiment"] = 0.0

    # Technical pass (per symbol) + inputs for the cross-sectional fundamental pass.
    tech: dict[str, TechnicalScore] = {}
    bars_by_symbol: dict[str, pd.DataFrame] = {}
    feature_rows: dict[str, pd.Series] = {}
    ratios: dict[str, dict] = {}
    above_200 = counted = 0
    for symbol in universe["symbol"]:
        bars = store.read_bars(market, symbol)
        if bars is None:
            continue
        bars = bars.loc[:cutoff]
        if bars.empty or bars.index[-1] < cutoff - pd.Timedelta(days=7):
            continue  # stale or not yet listed on asof
        bars_by_symbol[symbol] = bars
        row = technical_features(bars, benchmark_bars).iloc[-1]
        feature_rows[symbol] = row
        if pd.notna(row["sma200"]):
            counted += 1
            above_200 += bool(row["close"] > row["sma200"])
        if (t := score_features(row)) is not None:
            tech[symbol] = t
        fin = financials.get_financials(market, symbol)
        if not fin.empty:
            ratios[symbol] = key_ratios(fin, asof, float(row["close"]))

    sectors = dict(zip(universe["symbol"], universe["sector"], strict=True))
    fund = score_universe(ratios, sectors)
    index_row = (
        technical_features(benchmark_bars).iloc[-1]
        if benchmark_bars is not None and len(benchmark_bars)
        else None
    )
    regime = compute_regime(index_row, above_200 / counted if counted else None)

    names = dict(zip(universe["symbol"], universe["name"], strict=True))
    scores: list[SymbolScore] = []
    ideas: list[Idea] = []
    for symbol in universe["symbol"]:
        row = feature_rows.get(symbol)
        results: dict[str, AnalystResult] = {}
        if symbol in tech:
            results["technical"] = _tech_result(tech[symbol])
        if symbol in fund:
            results["fundamental"] = _fund_result(fund[symbol])
        composite, confidence = combine(results, weights)
        liquid = (
            row is not None
            and pd.notna(row["traded_value_20"])
            and row["traded_value_20"] >= cfg.min_avg_traded_value
        )
        score = SymbolScore(
            symbol=symbol,
            name=names[symbol],
            sector=sectors[symbol],
            close=None if row is None else float(row["close"]),
            composite=None if composite is None else round(composite, 4),
            confidence=round(confidence, 4),
            liquid=bool(liquid),
            analysts=results,
        )
        scores.append(score)
        direction = decide(score, cfg, config, regime)
        if direction is None:
            continue
        levels = compute_levels(market, direction, bars_by_symbol[symbol], cfg.stop_loss, cfg.take_profit)
        if levels is None:
            continue
        ideas.append(
            Idea(
                symbol=symbol,
                name=score.name,
                sector=score.sector,
                direction=direction,
                composite=score.composite,
                confidence=score.confidence,
                levels=levels,
                reasons={k: r.reasons for k, r in results.items()},
            )
        )

    limit = config.strategy.max_ideas
    ranked = []
    for direction in Direction:
        side = sorted((i for i in ideas if i.direction is direction), key=lambda i: i.rank_key, reverse=True)
        ranked += side[:limit]
    log.info(
        "%s scan %s: %d scored, %d ideas, regime %s", market, asof, len(scores), len(ranked), regime.label
    )
    return ScanResult(market=market, asof=asof, regime=regime, weights=weights, ideas=ranked, scores=scores)


# Persistence: one JSON file per market per scan date.
def scan_dir(service: DataService, market: Market) -> Path:
    return Path(service.config.data.cache_dir).parent / "scans" / market.value


def save_scan(service: DataService, result: ScanResult) -> Path:
    path = scan_dir(service, result.market) / f"{result.asof.isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(result.model_dump_json())
    tmp.replace(path)
    return path


def load_latest_scan(service: DataService, market: Market) -> ScanResult | None:
    folder = scan_dir(service, market)
    files = sorted(folder.glob("*.json")) if folder.exists() else []
    if not files:
        return None
    return ScanResult.model_validate(json.loads(files[-1].read_text()))
