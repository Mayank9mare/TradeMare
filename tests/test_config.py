import pytest
from pydantic import ValidationError

from tests.conftest import REPO_CONFIG
from trademare.config import load_config
from trademare.config.loader import deep_merge
from trademare.config.schema import StopLossConfig, StopLossMethod
from trademare.core.models import Market


def test_repo_settings_load():
    config = load_config(REPO_CONFIG)
    assert set(config.markets) == {Market.IN, Market.US}
    assert config.markets[Market.IN].stop_loss.method is StopLossMethod.ATR
    assert config.llm.enabled is False


def test_local_override_is_deep_merged(base_config_dict, write_config):
    path = write_config(base_config_dict, local={"markets": {"IN": {"budget": 500000, "broker": "dhan"}}})
    config = load_config(path)
    india = config.markets[Market.IN]
    assert india.budget == 500000
    assert india.broker == "dhan"
    assert india.stop_loss.atr_multiple == 2.0  # untouched keys survive


def test_env_var_selects_config(base_config_dict, write_config, monkeypatch):
    base_config_dict["markets"]["US"]["budget"] = 4242
    monkeypatch.setenv("TRADEMARE_CONFIG", str(write_config(base_config_dict)))
    assert load_config().markets[Market.US].budget == 4242


def test_deployable_capital_respects_cash_buffer():
    config = load_config(REPO_CONFIG)
    india = config.markets[Market.IN]
    assert india.deployable_capital == pytest.approx(india.budget * 0.9)


@pytest.mark.parametrize(
    ("market", "patch", "message"),
    [
        ("IN", {"broker": "alpaca"}, "must be one of"),
        ("US", {"broker": "zerodha"}, "must be one of"),
        ("IN", {"allow_short": True}, "short_instrument: futures"),
        ("IN", {"allow_long": False, "allow_short": False}, "at least one"),
        ("US", {"budget": 0}, "greater than 0"),
        ("US", {"risk_per_trade_pct": 8, "max_position_pct": 5}, "cannot exceed max_position_pct"),
        ("US", {"unknown_key": 1}, "Extra inputs are not permitted"),
    ],
)
def test_invalid_market_config_rejected(base_config_dict, write_config, market, patch, message):
    base_config_dict["markets"][market].update(patch)
    with pytest.raises(ValidationError, match=message):
        load_config(write_config(base_config_dict))


def test_india_short_allowed_via_futures(base_config_dict, write_config):
    base_config_dict["markets"]["IN"].update(allow_short=True, short_instrument="futures")
    assert load_config(write_config(base_config_dict)).markets[Market.IN].allow_short


def test_fixed_amount_stop_requires_amount():
    with pytest.raises(ValidationError, match=r"requires stop_loss\.fixed_amount"):
        StopLossConfig(method="fixed_amount")
    assert StopLossConfig(method="fixed_amount", fixed_amount=12.5).fixed_amount == 12.5


def test_missing_config_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.yaml")


def test_deep_merge_replaces_non_dict_values():
    assert deep_merge({"a": {"b": 1, "c": [1]}}, {"a": {"c": [2]}}) == {"a": {"b": 1, "c": [2]}}
