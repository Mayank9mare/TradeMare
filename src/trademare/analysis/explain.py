"""Ask Claude about a signal: a plain-language explanation built only from the signal's own evidence.

The model sees what the dashboard shows (factor ranks, fundamentals, forensic scores, filings, risk
and sizing) and answers the owner's question, typically "why this pick?" or "what could go wrong?".
It is told not to fetch or invent facts and not to make the decision. Answers are stored with the
signal (``events`` table, kind ``ask``) and cost counts against ``llm.max_daily_cost_usd``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from trademare.analysis.llm import LLMError
from trademare.config.schema import AppConfig, LLMProvider
from trademare.config.secrets import Secrets

SYSTEM = (
    "You explain trade signals produced by a rules-based swing-trading system to the account owner, who "
    "decides whether to approve them. Use only the evidence provided; if it does not answer the "
    "question, say so rather than guessing, and do not state facts from outside it (prices, news, "
    "events). Be concrete: name the numbers that drove the signal and how they compare with peers. "
    "Point out risks and anything in the evidence that argues against the trade. Do not tell the "
    "owner to buy or sell; the decision is theirs. Plain English, no markdown, under 250 words."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "risks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["answer", "risks"],
    "additionalProperties": False,
}
MAX_EVIDENCE_CHARS = 24_000
MAX_QUESTION_CHARS = 500


@dataclass
class Answer:
    question: str
    answer: str
    risks: list[str]
    cost_usd: float


def llm_ready(config: AppConfig, secrets: Secrets) -> str | None:
    """None when a model can be called, else what is missing."""
    if config.llm.provider is LLMProvider.OPENAI:
        ok = secrets.openai_api_key or os.environ.get("OPENAI_API_KEY")
        return None if ok else "add OPENAI_API_KEY to .env"
    ok = secrets.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
    return None if ok else "add ANTHROPIC_API_KEY to .env to ask Claude about signals"


def signal_prompt(signal: Any, question: str) -> str:
    facts = {
        "market": signal.market,
        "symbol": signal.symbol,
        "action": signal.action,
        "direction": signal.direction,
        "scan_date": str(signal.scan_date),
        "quantity": signal.quantity,
        "entry_or_last_price": signal.entry_price,
        "stop_price": signal.stop_price,
        "target_price": signal.target_price,
        "score": signal.score,
        "exit_reason": signal.exit_reason,
        "reasons": signal.reasons,
    }
    evidence = json.dumps(signal.evidence or {}, default=str)
    if len(evidence) > MAX_EVIDENCE_CHARS:
        evidence = evidence[:MAX_EVIDENCE_CHARS] + " …(truncated)"
    return (
        f"Signal:\n{json.dumps(facts, default=str, indent=1)}\n\n"
        f"Evidence recorded with the signal:\n{evidence}\n\n"
        f"Owner's question: {question}"
    )


class SignalExplainer:
    def __init__(self, config: AppConfig, client, ledger_dir: str | Path) -> None:
        self.config, self.client = config, client
        self.ledger_dir = Path(ledger_dir)

    def _ledger(self, day: date) -> Path:
        return self.ledger_dir / f"ask-{day.isoformat()}.json"

    def spent_today(self, day: date | None = None) -> float:
        try:
            return float(json.loads(self._ledger(day or date.today()).read_text())["spent_usd"])
        except (OSError, ValueError, KeyError):
            return 0.0

    def ask(self, signal: Any, question: str, day: date | None = None) -> Answer:
        day = day or date.today()
        question = (question or "").strip()[:MAX_QUESTION_CHARS] or "Why this pick, and what could go wrong?"
        spent = self.spent_today(day)
        if spent >= self.config.llm.max_daily_cost_usd:
            cap = self.config.llm.max_daily_cost_usd
            raise LLMError(f"today's LLM budget (${cap:.2f}, llm.max_daily_cost_usd) is used up")
        result = self.client.complete_json(SYSTEM, signal_prompt(signal, question), SCHEMA)
        path = self._ledger(day)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"spent_usd": spent + result.cost_usd}))
        data = result.data
        risks = [str(r) for r in data.get("risks", []) if str(r).strip()]
        return Answer(question, str(data.get("answer", "")).strip(), risks, result.cost_usd)
