import pytest

from tests.conftest import REPO_CONFIG
from tests.test_web_trading import make_app
from trademare.brain import build_brief, recent_reviews, save_review
from trademare.config import Secrets, load_config
from trademare.data.service import DataService


def test_brief_lists_pending_signal_with_its_reasons(tmp_path, monkeypatch):
    _client, db = make_app(tmp_path, monkeypatch)
    config = load_config(REPO_CONFIG).model_copy(deep=True)
    config.data.cache_dir = str(tmp_path / "cache")
    service = DataService(config, Secrets(_env_file=None))
    brief = build_brief(config, db, service)
    india = brief["markets"]["IN"]
    [sig] = india["pending_signals"]
    assert sig["id"] == "sig1" and sig["action"] == "buy" and sig["quantity"] == 10
    assert sig["most_you_can_lose"] == 1000.0 and sig["cost"] == 15000.0
    assert sig["for"] == ["12-1 month momentum +40% (#1 of 200)"]
    assert india["holdings"] == [] and "health_problems" in brief


def test_review_is_saved_and_shown_on_the_signal_card(tmp_path, monkeypatch):
    client, db = make_app(tmp_path, monkeypatch)
    save_review(
        db, "sig1", "Should I take this trade?", "Take a smaller size: results on 3 Nov.", ["results gap"]
    )
    page = client.get("/signals").text
    assert "Take a smaller size: results on 3 Nov." in page and "results gap" in page
    assert recent_reviews(db)[0]["source"] == "claude-code"
    with pytest.raises(KeyError):
        save_review(db, "nope", "q", "a", [])


def test_outside_holdings_view_and_portfolio_section(tmp_path, monkeypatch):
    import json

    from trademare.execution import outside_holdings

    client, _db = make_app(tmp_path, monkeypatch)
    config = load_config(REPO_CONFIG).model_copy(deep=True)
    config.data.cache_dir = str(tmp_path / "cache")
    path = outside_holdings.snapshot_path(config)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"broker": "zerodha", "fetched_at": "2026-10-27T05:00:00+00:00", "holdings": [
        {"symbol": "INFY", "exchange": "NSE", "quantity": 5, "average_price": 1190.5, "last_price": 1166.0,
         "day_change_pct": 0.9},
    ]}))  # fmt: skip
    v = outside_holdings.view(config, DataService(config, Secrets(_env_file=None)))
    [row] = v["rows"]
    assert row["value"] == 5 * 1166.0 and round(row["return"], 4) == round(1166 / 1190.5 - 1, 4)
    page = client.get("/portfolio/IN").text
    assert "Your other stocks at Zerodha" in page and "INFY" in page and "Refresh from Zerodha" in page
    assert "Your other stocks" not in client.get("/portfolio/US").text
