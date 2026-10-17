import re
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from tests.conftest import REPO_CONFIG
from trademare.config import Secrets, load_config
from trademare.data.service import DataService
from trademare.storage.db import Database, OrderRow, SignalRow
from trademare.web.app import create_app


def make_app(tmp_path, monkeypatch, password=None):
    universes = tmp_path / "config" / "universes"
    universes.mkdir(parents=True, exist_ok=True)
    for name in ("nifty500", "sp500"):
        (universes / f"{name}.csv").write_text("symbol,name,sector,cik\nINFY,Infosys,IT,\n")
    monkeypatch.chdir(tmp_path)
    config = load_config(REPO_CONFIG).model_copy(deep=True)
    config.data.cache_dir = str(tmp_path / "cache")
    secrets = Secrets(_env_file=None, dashboard_password=SecretStr(password) if password else None)
    db = Database(f"sqlite:///{tmp_path / 'web.db'}")
    with db.session() as s:
        s.add(
            SignalRow(
                id="sig1",
                market="IN",
                symbol="INFY",
                direction="LONG",
                action="ENTRY",
                quantity=10,
                entry_price=1500.0,
                stop_price=1400.0,
                target_price=None,
                score=0.9,
                confidence=1.0,
                reasons={"factor": ["12-1 month momentum +40% (#1 of 200)"]},
                status="PENDING",
                scan_date=date(2025, 6, 2),
                expires_at=datetime(2099, 1, 1, tzinfo=UTC),
            )
        )
    app = create_app(config, DataService(config, secrets), db, secrets)
    return TestClient(app), db


def token(html: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def test_approve_via_form(tmp_path, monkeypatch):
    client, db = make_app(tmp_path, monkeypatch)
    page = client.get("/signals")
    assert page.status_code == 200 and "INFY" in page.text and "12-1 month momentum" in page.text
    r = client.post(
        "/signals/sig1/approve", data={"csrf_token": token(page.text), "quantity": 5}, follow_redirects=False
    )
    assert r.status_code == 303 and "Approved" in r.headers["location"]
    with db.session() as s:
        order = s.query(OrderRow).one()
        assert order.quantity == 5 and order.limit_price == 1500.0 and order.status == "OPEN"
        assert s.get(SignalRow, "sig1").status == "APPROVED"
    # Second approval is refused with a message, not an error page.
    r = client.post("/signals/sig1/approve", data={"csrf_token": token(page.text)}, follow_redirects=False)
    assert "err=" in r.headers["location"]


def test_bad_csrf_token_rejected(tmp_path, monkeypatch):
    client, db = make_app(tmp_path, monkeypatch)
    assert client.post("/signals/sig1/approve", data={"csrf_token": "forged"}).status_code == 403
    with db.session() as s:
        assert s.get(SignalRow, "sig1").status == "PENDING"


def test_reject_and_portfolio(tmp_path, monkeypatch):
    client, db = make_app(tmp_path, monkeypatch)
    t = token(client.get("/signals").text)
    assert (
        client.post("/signals/sig1/reject", data={"csrf_token": t}, follow_redirects=False).status_code == 303
    )
    with db.session() as s:
        assert s.get(SignalRow, "sig1").status == "REJECTED"
    page = client.get("/portfolio/IN")
    assert page.status_code == 200 and "paper account" in page.text and "armed" in page.text
    assert client.get("/api/portfolio/IN/equity").json() == {"equity": []}
    assert (
        client.post("/portfolio/IN/rearm", data={"csrf_token": t}, follow_redirects=False).status_code == 303
    )


@pytest.mark.parametrize("path", ["/signals", "/portfolio/IN", "/settings"])
def test_password_protects_dashboard(tmp_path, monkeypatch, path):
    client, _ = make_app(tmp_path, monkeypatch, password="s3cret")
    assert client.get(path).status_code == 401
    assert client.get(path, auth=("me", "wrong")).status_code == 401
    assert client.get(path, auth=("me", "s3cret")).status_code == 200
