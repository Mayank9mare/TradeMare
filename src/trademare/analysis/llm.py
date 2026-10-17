"""LLM clients behind one interface: Claude (Anthropic SDK) or OpenAI.

Both return JSON constrained to a schema plus token usage and cost, so the
caller can enforce a daily budget.
"""

from __future__ import annotations

import json
import logging

from trademare.config.schema import LLMConfig, LLMProvider
from trademare.config.secrets import Secrets
from trademare.core.interfaces import LLMResult

log = logging.getLogger(__name__)

# USD per million tokens (input, output), Anthropic first-party prices as of 2026-10.
CLAUDE_PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-1": (10.0, 50.0),
}


class LLMError(Exception):
    """The model could not produce a usable answer (refusal, bad JSON, API failure)."""


def _parse(text: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError as e:
        raise LLMError(f"model returned invalid JSON: {text[:200]!r}") from e
    if not isinstance(data, dict):
        raise LLMError("model returned JSON that is not an object")
    return data


def _cost(config: LLMConfig, input_tokens: int, output_tokens: int) -> float:
    if config.input_usd_per_mtok is not None and config.output_usd_per_mtok is not None:
        prices = (config.input_usd_per_mtok, config.output_usd_per_mtok)
    else:
        # Unknown Claude model: assume the most expensive tier so the budget cap stays conservative.
        prices = CLAUDE_PRICES.get(config.model, CLAUDE_PRICES["claude-fable-5-1"])
    return (input_tokens * prices[0] + output_tokens * prices[1]) / 1_000_000


class ClaudeClient:
    def __init__(self, config: LLMConfig, secrets: Secrets, client=None) -> None:
        self.config = config
        self.model = config.model
        if client is None:
            import anthropic

            key = secrets.anthropic_api_key.get_secret_value() if secrets.anthropic_api_key else None
            # Without a key the SDK falls back to ANTHROPIC_API_KEY / an `ant auth login` profile.
            client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
        self._client = client

    def complete_json(self, system: str, prompt: str, schema: dict) -> LLMResult:
        import anthropic

        try:
            response = self._client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                # Classification-sized task: low effort; the schema guarantees parseable JSON.
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
                # If a safety classifier declines, the API retries on a fallback model in the same call.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.APIStatusError as e:
            raise LLMError(f"Claude API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"Claude API unreachable: {e}") from e
        if response.stop_reason == "refusal":
            raise LLMError("Claude declined to answer")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise LLMError(f"no text in response (stop_reason {response.stop_reason})")
        usage = response.usage
        return LLMResult(
            data=_parse(text),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=_cost(self.config, usage.input_tokens, usage.output_tokens),
        )


class OpenAIClient:
    def __init__(self, config: LLMConfig, secrets: Secrets, client=None) -> None:
        self.config = config
        self.model = config.model
        if client is None:
            try:
                import openai
            except ImportError as e:
                raise LLMError(
                    "llm.provider is openai but the openai package is missing: uv add openai"
                ) from e
            key = secrets.openai_api_key.get_secret_value() if secrets.openai_api_key else None
            client = openai.OpenAI(api_key=key) if key else openai.OpenAI()
        self._client = client

    def complete_json(self, system: str, prompt: str, schema: dict) -> LLMResult:
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "answer", "schema": schema, "strict": True},
                },
            )
        except Exception as e:  # the openai package is optional, so its exception types may not exist
            raise LLMError(f"OpenAI API error: {e}") from e
        text = response.choices[0].message.content
        if not text:
            raise LLMError("OpenAI returned no content")
        usage = response.usage
        return LLMResult(
            data=_parse(text),
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            cost_usd=_cost(self.config, usage.prompt_tokens, usage.completion_tokens),
        )


def create_llm_client(config: LLMConfig, secrets: Secrets):
    if config.provider is LLMProvider.OPENAI:
        return OpenAIClient(config, secrets)
    return ClaudeClient(config, secrets)
