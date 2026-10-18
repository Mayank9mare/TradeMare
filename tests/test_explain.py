from datetime import date
from types import SimpleNamespace

import pytest

from tests.conftest import REPO_CONFIG
from trademare.analysis.explain import SignalExplainer
from trademare.analysis.llm import LLMError
from trademare.config import load_config
from trademare.core.interfaces import LLMResult


class Fake:
    def complete_json(self, system, prompt, schema):
        return LLMResult(data={"answer": "ok", "risks": []}, input_tokens=1, output_tokens=1, cost_usd=0.6)


def test_daily_budget_is_enforced(tmp_path):
    config = load_config(REPO_CONFIG)  # max_daily_cost_usd: 1.0
    sig = SimpleNamespace(
        market="IN", symbol="X", action="ENTRY", direction="LONG", scan_date=date(2026, 10, 16), quantity=1,
        entry_price=1.0, stop_price=0.9, target_price=None, score=0.5, exit_reason=None,
        reasons={}, evidence={},
    )  # fmt: skip
    ex = SignalExplainer(config, Fake(), tmp_path)
    day = date(2026, 10, 19)
    assert ex.ask(sig, "", day).question.startswith("Why this pick")  # empty question gets the default
    ex.ask(sig, "again", day)
    assert ex.spent_today(day) == pytest.approx(1.2)
    with pytest.raises(LLMError, match="budget"):
        ex.ask(sig, "third", day)
    assert ex.ask(sig, "next day", date(2026, 10, 20)).answer == "ok"
