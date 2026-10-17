from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from trademare.analysis.llm import ClaudeClient, LLMError
from trademare.analysis.news import Headline, parse_rss, search_name
from trademare.analysis.sentiment import SCHEMA, SentimentAnalyst
from trademare.config.schema import LLMConfig
from trademare.core.interfaces import LLMResult
from trademare.core.models import Direction, Market

RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Infosys wins large deal - Mint</title><source>Mint</source>
  <pubDate>Wed, 14 Oct 2026 07:00:00 GMT</pubDate></item>
<item><title>Older story - ET</title><source>ET</source>
  <pubDate>Mon, 12 Oct 2026 07:00:00 GMT</pubDate></item>
<item><title>Bad date</title><source>X</source><pubDate>not a date</pubDate></item>
</channel></rss>"""


def test_parse_rss_and_search_name():
    items = parse_rss(RSS, max_items=5)
    assert [h.title for h in items] == ["Infosys wins large deal", "Older story"]
    assert items[0].source == "Mint" and items[0].published.tzinfo is not None
    assert search_name("Infosys Ltd.") == "Infosys"
    assert search_name("Mahindra &amp; Mahindra Limited") == "Mahindra & Mahindra"


class FakeLLM:
    model = "fake"

    def __init__(self, data=None, error=None):
        self.data = data or {
            "score": 0.4,
            "confidence": 0.7,
            "red_flags": [],
            "summary": "Deal win reported.",
        }
        self.error, self.calls = error, 0

    def complete_json(self, system, prompt, schema):
        self.calls += 1
        if self.error:
            raise self.error
        assert schema is SCHEMA and "Headlines, newest first" in prompt
        return LLMResult(data=self.data, input_tokens=1000, output_tokens=100, cost_usd=0.01)


def headlines(*_args):
    return [Headline("Infosys wins large deal", "Mint", datetime(2026, 10, 14, tzinfo=UTC))]


def analyst(tmp_path, llm, news=headlines, **cfg):
    return SentimentAnalyst(LLMConfig(enabled=True, **cfg), llm, tmp_path, news=news)


DAY = date(2026, 10, 16)


def test_positive_news_passes_and_is_cached(tmp_path):
    llm = FakeLLM()
    a = analyst(tmp_path, llm)
    out = a.assess(Market.IN, "INFY", "Infosys Ltd.", Direction.LONG, DAY)
    assert not out.veto and out.result.score == 0.4 and out.cost_usd == 0.01
    again = a.assess(Market.IN, "INFY", "Infosys Ltd.", Direction.LONG, DAY)
    assert llm.calls == 1 and again.cost_usd == 0.0 and again.result.score == 0.4  # cached


@pytest.mark.parametrize(
    ("data", "direction", "veto"),
    [
        (
            {"score": 0.1, "confidence": 0.9, "red_flags": ["fraud_or_accounting"], "summary": "Probe."},
            Direction.LONG,
            True,
        ),
        (
            {"score": -0.6, "confidence": 0.9, "red_flags": [], "summary": "Guidance cut."},
            Direction.LONG,
            True,
        ),
        ({"score": -0.4, "confidence": 0.9, "red_flags": [], "summary": "Mild."}, Direction.LONG, False),
        ({"score": 0.7, "confidence": 0.9, "red_flags": [], "summary": "Great."}, Direction.SHORT, True),
        ({"score": -0.7, "confidence": 0.9, "red_flags": [], "summary": "Bad."}, Direction.SHORT, False),
    ],
)
def test_veto_rules(tmp_path, data, direction, veto):
    out = analyst(tmp_path, FakeLLM(data)).assess(Market.IN, "X", "X Ltd", direction, DAY)
    assert out.veto is veto


def test_budget_cap_stops_calls_without_veto(tmp_path):
    llm = FakeLLM()
    a = analyst(tmp_path, llm, max_daily_cost_usd=0.01)
    a.assess(Market.IN, "A", "A Ltd", Direction.LONG, DAY)
    out = a.assess(Market.IN, "B", "B Ltd", Direction.LONG, DAY)
    assert llm.calls == 1 and out.result is None and not out.veto and "budget" in out.note


def test_failures_never_block(tmp_path):
    def broken_news(*_):
        raise ConnectionError("offline")

    out = analyst(tmp_path, FakeLLM(), news=broken_news).assess(Market.IN, "A", "A", Direction.LONG, DAY)
    assert out.result is None and not out.veto and "news unavailable" in out.note
    out = analyst(tmp_path, FakeLLM(error=LLMError("refused"))).assess(
        Market.IN, "A", "A", Direction.LONG, DAY
    )
    assert out.result is None and not out.veto and "LLM error" in out.note
    out = analyst(tmp_path, FakeLLM(), news=lambda *_: []).assess(Market.IN, "A", "A", Direction.LONG, DAY)
    assert out.result is None and "no recent headlines" in out.note


# ---------------------------------------------------------------- Claude client request shape
class FakeAnthropic:
    def __init__(
        self, text='{"score": 0.2, "confidence": 0.5, "red_flags": [], "summary": "ok"}', stop="end_turn"
    ):
        self.kwargs = None
        self.text, self.stop = text, stop
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            stop_reason=self.stop,
            content=[SimpleNamespace(type="text", text=self.text)],
            usage=SimpleNamespace(input_tokens=2000, output_tokens=150),
        )


def test_claude_client_request_and_cost():
    fake = FakeAnthropic()
    client = ClaudeClient(LLMConfig(enabled=True), None, client=fake)
    result = client.complete_json("sys", "prompt", SCHEMA)
    kw = fake.kwargs
    assert kw["model"] == "claude-opus-5-5"
    assert kw["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}}
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert result.data["score"] == 0.2
    assert result.cost_usd == pytest.approx((2000 * 4 + 150 * 20) / 1e6)


@pytest.mark.parametrize(("text", "stop"), [("{}", "refusal"), ("not json", "end_turn"), ("[1]", "end_turn")])
def test_claude_client_errors(text, stop):
    with pytest.raises(LLMError):
        ClaudeClient(LLMConfig(enabled=True), None, client=FakeAnthropic(text, stop)).complete_json(
            "s", "p", SCHEMA
        )


def test_openai_provider_requires_model_and_prices():
    with pytest.raises(ValueError, match="OpenAI model"):
        LLMConfig(provider="openai")
    with pytest.raises(ValueError, match="input_usd_per_mtok"):
        LLMConfig(provider="openai", model="gpt-x")
    LLMConfig(provider="openai", model="gpt-x", input_usd_per_mtok=1, output_usd_per_mtok=2)
