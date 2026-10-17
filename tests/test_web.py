import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tests.conftest import REPO_CONFIG
from trademare.config import Secrets, load_config
from trademare.core.models import Market
from trademare.data.fields import normalize_financials
from trademare.data.quality import clean_bars
from trademare.data.service import DataService
from trademare.web.app import create_app
from trademare.web.formatting import money, pct


@pytest.fixture
def client(tmp_path, monkeypatch):
    universes = tmp_path / "config" / "universes"
    universes.mkdir(parents=True)
    india = "symbol,name,sector,cik\nM&M,Mahindra,Automobile,\nINFY,Infosys,IT,\n"
    for name in ("nifty200", "nifty500"):
        (universes / f"{name}.csv").write_text(india)
    (universes / "sp500.csv").write_text("symbol,name,sector,cik\nAAPL,Apple,Tech,0000320193\n")
    monkeypatch.chdir(tmp_path)  # universes resolve relative to cwd

    config = load_config(REPO_CONFIG).model_copy(deep=True)
    config.data.cache_dir = str(tmp_path / "cache")
    service = DataService(config, Secrets(_env_file=None))

    idx = pd.bdate_range("2024-01-01", "2025-06-30")
    close = pd.Series(range(len(idx)), index=idx, dtype=float) + 100
    bars = clean_bars(
        pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1e6})
    )
    service.store.write_bars(Market.IN, "M&M", bars)
    service.store.write_bars(Market.US, "AAPL", bars)
    fin = normalize_financials(
        pd.DataFrame(
            [
                dict(
                    period_type="A",
                    period_end="2024-03-31",
                    available_from="2024-05-30",
                    revenue=1e10,
                    net_income=1e9,
                    total_equity=5e9,
                    shares_outstanding=1e8,
                ),
            ]
        )
    )
    service.store.write_financials(Market.IN, "yfinance", "M&M", fin)
    return TestClient(create_app(config, service, config_path=REPO_CONFIG))


def test_overview(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "India" in r.text and "1 / 2" in r.text  # bars cached / universe


def test_market_table(client):
    r = client.get("/m/IN")
    assert r.status_code == 200
    assert "M%26M" in r.text  # symbol URL-encoded in links
    assert "INFY" in r.text  # listed even without data


def test_symbol_page_and_api(client):
    r = client.get("/m/IN/M%26M")
    assert r.status_code == 200 and "Mahindra" in r.text and "Key ratios" in r.text
    data = client.get("/api/bars/IN/M%26M", params={"years": 1}).json()
    assert data["candles"][-1]["time"] == "2025-06-30"
    assert len(data["sma200"]) > 0


def test_unknown_symbol_and_market(client):
    assert client.get("/m/IN/NOPE").status_code == 404
    assert client.get("/m/XX").status_code == 404


def test_settings(client):
    assert "risk_per_trade_pct" in client.get("/settings").text


def test_formatting():
    assert money(2.5e11, Market.IN) == "₹25,000 Cr"
    assert money(3.2e9, Market.US) == "$3.2B"
    assert money(None, Market.US) == "—"
    assert pct(0.1234, 1, True) == "+12.3%"


def test_filters_treat_missing_values_as_blank():
    from jinja2 import Environment

    from trademare.web.formatting import num, tone

    missing = Environment().undefined()
    assert pct(missing) == "—" and num(float("nan")) == "—" and tone(missing) == ""
    assert money(None, Market.US) == "—"


def test_backtest_list_tolerates_runs_without_splits(tmp_path, monkeypatch):
    import json

    from fastapi.testclient import TestClient

    from trademare.config import Secrets
    from trademare.storage.db import Database

    monkeypatch.chdir(tmp_path)
    config = load_config(REPO_CONFIG).model_copy(deep=True)
    config.data.cache_dir = str(tmp_path / "data" / "cache")
    run = tmp_path / "data" / "backtests" / "IN-x"
    run.mkdir(parents=True)
    summary = {"strategy": {"cagr": 0.05}, "benchmark": {}, "exit_reasons": {}, "by_year": {}}
    meta = {
        "id": "IN-x",
        "market": "IN",
        "start": "2025-01-01",
        "end": "2025-12-31",
        "created_at": "2026-01-01T00:00:00",
        "initial_capital": 1.0,
        "final_equity": 1.0,
        "overrides": [],
        "open_positions": 0,
        "summary": summary,
    }
    (run / "summary.json").write_text(json.dumps(meta))
    secrets = Secrets(_env_file=None)
    app = create_app(
        config, DataService(config, secrets), Database("sqlite:///:memory:"), secrets, config_path=REPO_CONFIG
    )
    page = TestClient(app).get("/backtests")
    assert page.status_code == 200 and "+5.0%" in page.text
