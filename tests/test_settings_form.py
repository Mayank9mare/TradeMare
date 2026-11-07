import re
import shutil

import pytest
import yaml
from fastapi.testclient import TestClient

from tests.conftest import REPO_CONFIG
from trademare.config import Secrets, load_config
from trademare.data.service import DataService
from trademare.storage.db import Database
from trademare.web.app import create_app
from trademare.web.settings_form import apply_form, current_values


@pytest.fixture
def config_copy(tmp_path):
    path = tmp_path / "settings.yaml"
    shutil.copy(REPO_CONFIG, path)
    (tmp_path / "settings.local.yaml").write_text("markets:\n  US:\n    budget: 5000\n# kept\n")
    return path


def form_from(path):
    return {
        k: (
            "on"
            if v is True
            else ""
            if v in (False, None)
            else ", ".join(v)
            if isinstance(v, list)
            else str(v)
        )
        for k, v in current_values(load_config(path)).items()
    }


def test_unchanged_form_writes_nothing(config_copy):
    before = (config_copy.parent / "settings.local.yaml").read_text()
    assert apply_form(form_from(config_copy), config_copy) == ({}, [])
    assert (config_copy.parent / "settings.local.yaml").read_text() == before


def test_changes_merge_into_local_file(config_copy):
    form = form_from(config_copy)
    form.update({"IN.budget": "250000", "IN.stop_loss.method": "percent", "notifications.email.to": "a@b.c"})
    changes, errors = apply_form(form, config_copy)
    assert errors == []
    assert changes == {
        "markets": {"IN": {"budget": 250000.0, "stop_loss": {"method": "percent"}}},
        "notifications": {"email": {"to": ["a@b.c"]}},
    }
    local = yaml.safe_load((config_copy.parent / "settings.local.yaml").read_text())
    assert local["markets"]["US"]["budget"] == 5000  # existing override kept
    config = load_config(config_copy)
    assert config.markets["IN"].budget == 250000 and config.markets["IN"].stop_loss.method == "percent"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("IN.budget", "abc", "IN.budget"),
        ("IN.budget", "-5", "budget"),
        ("IN.stop_loss.method", "magic", "one of"),
    ],
)
def test_invalid_values_rejected_and_nothing_written(config_copy, field, value, message):
    before = (config_copy.parent / "settings.local.yaml").read_text()
    form = form_from(config_copy) | {field: value}
    changes, errors = apply_form(form, config_copy)
    assert changes == {} and any(message in e for e in errors)
    assert (config_copy.parent / "settings.local.yaml").read_text() == before


def test_validated_and_broker_are_not_editable(config_copy):
    form = form_from(config_copy) | {"IN.validated": "on", "IN.broker": "zerodha"}
    assert apply_form(form, config_copy) == ({}, [])


def test_settings_page_saves_via_form(config_copy, tmp_path, monkeypatch):
    universes = tmp_path / "config" / "universes"
    universes.mkdir(parents=True)
    for name in ("nifty500", "sp500"):
        (universes / f"{name}.csv").write_text("symbol,name,sector,cik\nINFY,Infosys,IT,\n")
    monkeypatch.chdir(tmp_path)
    config = load_config(config_copy)
    secrets = Secrets(_env_file=None)
    client = TestClient(
        create_app(
            config,
            DataService(config, secrets),
            Database("sqlite:///:memory:"),
            secrets,
            config_path=config_copy,
        )
    )
    page = client.get("/settings")
    assert page.status_code == 200 and "Save settings" in page.text
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    form = form_from(config_copy) | {"csrf_token": token, "IN.max_open_positions": "12"}
    r = client.post("/settings", data=form, follow_redirects=False)
    assert r.status_code == 303 and "max_open_positions" in r.headers["location"]
    assert load_config(config_copy).markets["IN"].max_open_positions == 12
    bad = client.post("/settings", data=form | {"IN.max_open_positions": "zero"})
    assert bad.status_code == 422 and "Not saved" in bad.text
    assert client.post("/settings", data=form | {"csrf_token": "forged"}).status_code == 403


def test_tested_values_flag_drift():
    from trademare.web.settings_form import TESTED, drifted, untested

    values = {k: v for k, v in TESTED.items()}
    assert drifted(values) == set() and untested(values) == []
    values |= {"IN.stop_loss.atr_multiple": "2.5", "strategy.vol_target": "", "IN.time_stop_days": ""}
    assert drifted(values) == {"IN.stop_loss.atr_multiple", "strategy.vol_target"}
    assert untested(values) == [
        "Stop: ATR multiple is 2.5 (tested with 3.0)",
        "Volatility targeting is blank (tested with on)",
    ]
