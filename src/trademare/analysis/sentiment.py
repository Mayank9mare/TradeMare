"""News-sentiment overlay (step 8).

For each candidate entry, recent headlines are scored by an LLM into a
sentiment score, a confidence, and specific red flags. It is an overlay, not
part of the ranking: it can veto an entry (red flag, or a score at or below
``llm.veto_score``) and adds its summary to the signal's reasons. Results are
cached per symbol per day, and calls stop once ``llm.max_daily_cost_usd`` is
spent (remaining stocks are then neither vetoed nor scored).

There is no free historical news archive, so this cannot be backtested; its
value is measured forward in paper trading (vetoes are logged with the idea).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from trademare.analysis.llm import LLMError
from trademare.analysis.news import Headline, fetch_headlines
from trademare.config.schema import LLMConfig
from trademare.core.interfaces import AnalystResult, LLMClient
from trademare.core.models import Direction, Market

log = logging.getLogger(__name__)

RED_FLAGS = (
    "fraud_or_accounting",
    "regulatory_action",
    "auditor_resignation",
    "default_or_insolvency",
    "major_litigation",
    "guidance_cut",
    "management_exit",
    "promoter_pledge_or_selling",
)

SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "number", "description": "-1 very negative .. +1 very positive for the stock"},
        "confidence": {"type": "number", "description": "0 .. 1, how informative the headlines are"},
        "red_flags": {"type": "array", "items": {"type": "string", "enum": list(RED_FLAGS)}},
        "summary": {"type": "string", "description": "One or two sentences, plain language"},
    },
    "required": ["score", "confidence", "red_flags", "summary"],
    "additionalProperties": False,
}

SYSTEM = (
    "You assess recent news about one listed company for a swing trader who holds positions for days to "
    "weeks. Judge only what the headlines and exchange filings say; do not use prior knowledge of the "
    "company or its price. Official filings are authoritative; headlines may be rumour or recycled. "
    "Score the likely impact on the stock over the next few weeks from -1 (clearly negative) to +1 "
    "(clearly positive); routine coverage, price-recap articles and analyst target lists are close to 0. "
    "Set confidence low when headlines are few, stale or uninformative. List a red flag only when a "
    "headline or filing directly reports it. Keep the summary to two sentences."
)


@dataclass
class SentimentOutcome:
    result: AnalystResult | None
    veto: bool = False
    note: str = ""
    cost_usd: float = 0.0
    headlines: list[Headline] = field(default_factory=list)


class SentimentAnalyst:
    def __init__(
        self,
        config: LLMConfig,
        client: LLMClient,
        cache_dir: Path,
        news: Callable[[Market, str, int, int], list[Headline]] = fetch_headlines,
    ) -> None:
        self.config = config
        self.client = client
        self.cache_dir = cache_dir
        self.news = news

    # Per-day ledger: {"spent_usd": float, "results": {symbol: {...}}}
    def _ledger_path(self, market: Market, day: date) -> Path:
        return self.cache_dir / market.value / f"{day.isoformat()}.json"

    def _load(self, market: Market, day: date) -> dict:
        path = self._ledger_path(market, day)
        return json.loads(path.read_text()) if path.exists() else {"spent_usd": 0.0, "results": {}}

    def _save(self, market: Market, day: date, ledger: dict) -> None:
        path = self._ledger_path(market, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ledger, indent=1, default=str))

    def assess(
        self,
        market: Market,
        symbol: str,
        company: str,
        direction: Direction,
        day: date,
        filings: list[dict] | None = None,
    ) -> SentimentOutcome:
        """``filings``: recent official announcements ({day, category, text}), newest first."""
        filings = filings or []
        ledger = self._load(market, day)
        try:
            headlines = self.news(market, company, self.config.lookback_days, self.config.max_headlines)
        except Exception as e:  # network/RSS problems must never block trading
            if not filings:
                return SentimentOutcome(None, note=f"news unavailable ({e.__class__.__name__}); not assessed")
            headlines = []  # official filings alone are still worth assessing
        if not headlines and not filings:
            return SentimentOutcome(None, note="no recent headlines or filings; not assessed")
        texts = [h.title for h in headlines] + [f"{f['day']} {f['category']} {f['text']}" for f in filings]
        digest = hashlib.sha256("\n".join(texts).encode()).hexdigest()[:16]
        cached = ledger["results"].get(symbol)
        if cached and cached.get("digest") == digest:
            data, cost = cached["data"], 0.0
        else:
            if ledger["spent_usd"] >= self.config.max_daily_cost_usd:
                return SentimentOutcome(
                    None, note="daily LLM budget reached; not assessed", headlines=headlines
                )
            try:
                answer = self.client.complete_json(
                    SYSTEM, _prompt(market, symbol, company, headlines, filings), SCHEMA
                )
            except LLMError as e:
                return SentimentOutcome(None, note=f"LLM error ({e}); not assessed", headlines=headlines)
            data, cost = answer.data, answer.cost_usd
            ledger["spent_usd"] += cost
            ledger["results"][symbol] = {"digest": digest, "data": data, "cost_usd": cost}
            self._save(market, day, ledger)

        score = max(-1.0, min(1.0, float(data.get("score", 0.0))))
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
        flags = [f for f in data.get("red_flags", []) if f in RED_FLAGS]
        reasons = [str(data.get("summary", "")).strip()] + [f"Red flag: {f.replace('_', ' ')}" for f in flags]
        result = AnalystResult(
            analyst="sentiment",
            score=round(score, 4),
            confidence=round(confidence, 4),
            reasons=[r for r in reasons if r],
            details={"headlines": float(len(headlines))},
        )
        threshold = self.config.veto_score
        # Longs: any red flag or clearly negative news; shorts: clearly positive news.
        long = direction is Direction.LONG
        veto = (bool(flags) or score <= threshold) if long else (score >= -threshold)
        note = f"sentiment {score:+.2f} from {len(headlines)} headlines, {len(filings)} filings" + (
            f"; red flags: {', '.join(flags)}" if flags else ""
        )
        return SentimentOutcome(result, veto=veto, note=note, cost_usd=cost, headlines=headlines)


def _prompt(
    market: Market, symbol: str, company: str, headlines: list[Headline], filings: list[dict] | None = None
) -> str:
    lines = "\n".join(f"- {h.published:%Y-%m-%d} [{h.source}] {h.title}" for h in headlines) or "(none)"
    exchange = "NSE (India)" if market is Market.IN else "US"
    source = "NSE corporate announcements" if market is Market.IN else "SEC filings"
    official = "\n".join(f"- {f['day']} [{f['category']}] {f['text']}" for f in filings or []) or "(none)"
    return (
        f"Company: {company} ({symbol}, {exchange})\n\n"
        f"Official filings ({source}), newest first:\n{official}\n\n"
        f"Headlines, newest first:\n{lines}"
    )
