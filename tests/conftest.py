from pathlib import Path

import pytest
import yaml

REPO_CONFIG = Path(__file__).parent.parent / "config" / "settings.yaml"


@pytest.fixture
def base_config_dict() -> dict:
    return yaml.safe_load(REPO_CONFIG.read_text())


@pytest.fixture
def write_config(tmp_path):
    """Write a config dict to a temp settings.yaml (and optional local override)."""

    def _write(data: dict, local: dict | None = None) -> Path:
        path = tmp_path / "settings.yaml"
        path.write_text(yaml.safe_dump(data))
        if local is not None:
            (tmp_path / "settings.local.yaml").write_text(yaml.safe_dump(local))
        return path

    return _write


@pytest.fixture(autouse=True)
def no_event_network(monkeypatch, request):
    """Tests never call NSE, SEC or Yahoo for corporate events; tests that need events stub them."""
    if "live_events" in request.keywords:
        return
    from datetime import UTC, datetime

    from trademare.data.events import CorporateEvents, EventsService

    def offline(self, market, symbol, today):
        return CorporateEvents(market.value, symbol, datetime.now(UTC))

    monkeypatch.setattr(EventsService, "_fetch", offline)


@pytest.fixture(autouse=True)
def ignore_personal_overrides(monkeypatch, tmp_path_factory):
    """The repo's config/settings.local.yaml holds the owner's personal choices; tests use the
    committed settings only. Overrides next to temporary configs still work."""
    from trademare.config import loader
    from trademare.web import settings_form

    real = loader.local_override_path
    elsewhere = tmp_path_factory.mktemp("no-local") / "settings.local.yaml"

    def override_path(path):
        return elsewhere if Path(path).resolve() == REPO_CONFIG.resolve() else real(Path(path))

    monkeypatch.setattr(loader, "local_override_path", override_path)
    monkeypatch.setattr(settings_form, "local_override_path", override_path)


@pytest.fixture(autouse=True)
def no_surveillance_network(monkeypatch):
    """Pre-trade checks never call NSE in tests; tests that need the lists stub them."""
    from trademare.data import surveillance

    monkeypatch.setattr(surveillance.SurveillanceService, "flagged", lambda self, today=None: {})


@pytest.fixture(autouse=True)
def no_split_network(monkeypatch):
    """Corporate-action checks never call Yahoo in tests; tests that need splits set desk.split_source."""
    import pandas as pd

    from trademare.trading.desk import TradingDesk

    monkeypatch.setattr(TradingDesk, "split_source", staticmethod(lambda symbol: pd.Series(dtype=float)))
