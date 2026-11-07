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
    assert "India" in r.text and "<b>1/2</b>" in r.text  # bars cached / universe
    assert "all caught up" in r.text  # no pending signals


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


def test_setups_page_shows_record_and_matches(client, monkeypatch):
    import json
    from pathlib import Path

    from trademare.strategy import setup_study
    from trademare.web import setup_routes

    monkeypatch.setattr(setup_routes, "current_record", lambda *a, **k: None)  # no background study
    stats = {
        "trades": 150,
        "win_rate": 0.45,
        "avg_ret": 0.012,
        "median_ret": 0.0,
        "avg_r": 0.2,
        "avg_bench": 0.008,
        "profit_factor": 1.3,
        "median_days": 20,
        "best": 0.3,
        "worst": -0.1,
    }
    records = {
        sid: {
            "setup": sid,
            "periods": {p: stats for p in setup_study.PERIODS},
            "by_year": {},
            "passes": sid == "vcp",
            "verdict": "x",
            "recent": [],
        }
        for sid in setup_study.SETUPS
    }
    match = {
        "setup": "vcp",
        "symbol": "M&M",
        "name": "Mahindra",
        "sector": "Automobile",
        "signal_date": "2025-06-30",
        "close": 100.0,
        "entry": 101.0,
        "stop": 95.0,
        "target": 119.0,
        "risk_pct": 0.059,
        "strength": 1.0,
        "liquid": True,
    }
    result = {
        "market": "IN",
        "data_through": "2025-06-30",
        "rules_version": "old",
        "records": records,
        "matches": [match, {**match, "setup": "ichimoku", "target": None}],
    }
    path = Path("cache") / "setups" / "IN-record.json"  # the fixture's cache_dir, relative to its cwd
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result))

    r = client.get("/setups/IN")
    assert r.status_code == 200
    assert "Volatility contraction (VCP)" in r.text and "Rally, quiet base, breakout" in r.text
    assert "M&amp;M" in r.text  # the passing setup is chosen by default, so its match shows
    assert "This record is from 2025-06-30" in r.text or "Updating the record" in r.text
    both = client.get("/setups/IN?s=vcp&s=ichimoku")
    assert both.text.count("Mahindra") == 1 and "Ichimoku cloud</span>" in both.text
    none = client.get("/setups/IN?s=pin_bar_range")
    assert "No liquid stock matched" in none.text
    assert client.get("/setups/XX").status_code == 404
    assert client.get("/setups", follow_redirects=False).status_code == 307


def test_setups_trading_form_saves_personal_settings(client, monkeypatch):
    import re

    from trademare.config import load_config
    from trademare.web import setup_routes

    monkeypatch.setattr(setup_routes, "current_record", lambda *a, **k: None)
    page = client.get("/setups/IN")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    form = {
        "csrf_token": token,
        "enabled": "on",
        "selected": ["vcp", "rally_base_breakout"],
        "budget_pct": "25",
        "max_positions": "2",
        "require_record": "on",
    }
    r = client.post("/setups/IN/trading", data=form, follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    saved = load_config(REPO_CONFIG).markets[Market.IN].setups
    assert saved.enabled and saved.selected == ["vcp", "rally_base_breakout"] and saved.max_positions == 2
    assert "Let setups propose paper trades" in client.get("/setups/IN").text

    nothing = client.post(
        "/setups/IN/trading", data={"csrf_token": token, "enabled": "on"}, follow_redirects=False
    )
    assert "err=" in nothing.headers["location"]
    assert client.post("/setups/IN/trading", data={**form, "csrf_token": "forged"}).status_code == 403


def test_strategy_overlay_on_chart(client):
    page = client.get("/m/IN/M%26M").text
    assert 'id="strategy-select"' in page and 'value="vcp"' in page and 'value="core"' in page
    r = client.get("/api/strategy/IN/M%26M", params={"id": "supertrend_rsi", "years": 3})
    assert r.status_code == 200
    s = r.json()
    names = [line["name"] for line in s["lines"]]
    assert names[0] == "Supertrend" and "Setup stop" in names
    assert {"trades", "closed", "wins", "avg_ret", "compounded"} <= set(s["stats"])
    # a steady uptrend gives the trend setup at least one trade, drawn as a buy marker
    assert s["stats"]["trades"] >= 1 and any(m["buy"] for m in s["markers"])
    assert all(t["entry_date"] <= t["exit_date"] for t in s["trades"])
    core = client.get("/api/strategy/IN/M%26M", params={"id": "core"}).json()
    assert core["name"] == "Core strategy" and core["signals"] == []
    assert client.get("/api/strategy/IN/M%26M", params={"id": "nope"}).status_code == 404


def test_overview_today_money_and_coming_up(client):
    page = client.get("/").text
    assert 'id="today-h"' in page and 'id="money-h"' in page and 'id="coming-h"' in page
    assert "all caught up" in page  # no signals, stops or results due
    assert "India paper account" in page and "Zerodha portfolio" not in page  # no snapshot yet
    assert "Nothing scheduled" in page and "No orders in the last 7 days" in page


def test_today_tiles_split_by_market_and_list_results(monkeypatch):
    from datetime import UTC, date, datetime
    from types import SimpleNamespace

    from trademare.web.overview_data import today_tiles

    config = load_config(REPO_CONFIG)
    sig = lambda m, action="ENTRY": SimpleNamespace(  # noqa: E731
        market=m, action=action, expires_at=datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
    )
    pending = [sig("IN"), sig("IN"), sig("IN", "EXIT"), sig("US")]
    zerodha = {"symbols": ["TATASTEEL"]}

    class Events:
        def cached(self, market, symbol):
            return SimpleNamespace(next_results=date(2026, 11, 7)) if symbol == "TATASTEEL" else None

    class Db:
        def session(self):
            import contextlib

            @contextlib.contextmanager
            def cm():
                yield SimpleNamespace(scalars=lambda q: [])

            return cm()

    import trademare.web.overview_data as od

    monkeypatch.setattr(od, "_open", lambda s, market: [])
    monkeypatch.setattr(od, "stops_raised_recently", lambda db, days=3: [{"symbol": "GESHIP"}])
    tiles, upcoming = today_tiles(config, Db(), pending, Events(), zerodha, [], date(2026, 11, 6))
    labels = {t.label: t for t in tiles}
    assert labels["India signals"].value == "3" and "2 buy · 1 sell" in labels["India signals"].sub
    assert "decide by Mon 9 Nov, 3:30 pm" in labels["India signals"].sub
    assert labels["US signals"].value == "1"
    assert labels["Raise your stop-loss"].sub.startswith("GESHIP")
    assert labels["Results this week"].sub == "TATASTEEL Sat 7 Nov"
    assert [u.kind for u in upcoming][:1] == ["results"]
