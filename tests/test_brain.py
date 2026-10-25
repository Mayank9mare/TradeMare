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
