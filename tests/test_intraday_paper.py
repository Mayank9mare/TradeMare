"""Intraday module, phase 4: live paper ledger, polling and the dashboard."""

import json
import re
from datetime import datetime

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tests.test_intraday_strategies import walk
from trademare.intraday import config as cfgmod
from trademare.intraday import paper
from trademare.intraday.sim import Trade
from trademare.intraday.store import CandleStore


@pytest.fixture
def cfg_path(tmp_path):
    path = tmp_path / "intraday.yaml"
    path.write_text(f"data_dir: {tmp_path / 'data'}\nhistory_start: 2025-01-01\n")
    root = tmp_path / "data"
    root.mkdir()
    (root / "nifty50.csv").write_text("symbol,name,sector,cik\nAAA,A Ltd,X,\nBBB,B Ltd,Y,\n")
    return path


def trade(entry_time, exit_time, symbol="AAA", entry=100.0, stop=99.0, exit_=101.0, reason="target", side=1):
    return Trade("orb", symbol, side, entry_time[:10], entry_time, entry_time, entry, stop, None, exit_time,
                 exit_, reason, 3, 0.01, 1.0)  # fmt: skip


def test_sizing_and_the_open_position_limit(cfg_path):
    cfg = cfgmod.load(cfg_path)
    assert paper.size(cfg, 100.0, 99.0) == 500  # ₹500 risk / ₹1 a share, under the 50% value cap
    assert paper.size(cfg, 100.0, 99.99) == 500  # capped by value: ₹50,000 / ₹100
    many = [
        trade(f"2026-01-05 10:{m:02d}:00", "2026-01-05 14:00:00", symbol=f"S{m}") for m in range(0, 30, 5)
    ]
    rows = paper.ledger(cfg, many, live=False)
    assert sum(r["taken"] for r in rows) == 5 and "positions already open" in rows[-1]["note"]
    later = paper.ledger(
        cfg, [*many[:5], trade("2026-01-05 14:30:00", "2026-01-05 15:00:00", symbol="Z")], live=False
    )
    assert later[-1]["taken"]  # earlier ones closed by then
    win = rows[0]
    assert win["pnl"] == pytest.approx(500 - win["charges"]) and win["status"] == "closed"
    still = paper.ledger(cfg, [trade("2026-01-05 10:00:00", "2026-01-05 11:00:00", reason="end")], live=True)
    assert still[0]["status"] == "open"


def test_strategies_need_the_evidence_bar(cfg_path):
    cfgmod.save_local({"paper": {"strategies": ["orb", "cpr"]}}, cfg_path)
    cfg = cfgmod.load(cfg_path)
    record = {"records": {"orb": {"passes": True}, "cpr": {"passes": False}}}
    assert paper.allowed_strategies(cfg, record) == (["orb"], ["cpr"])
    assert paper.allowed_strategies(cfg, None) == ([], ["orb", "cpr"])
    cfgmod.save_local({"paper": {"require_record": False}}, cfg_path)
    assert paper.allowed_strategies(cfgmod.load(cfg_path), None) == (["orb", "cpr"], [])


def test_poll_drops_the_forming_candle_and_stores_the_day_after_the_close(cfg_path):
    cfgmod.save_local({"paper": {"strategies": ["orb"], "require_record": False}}, cfg_path)
    cfg = cfgmod.load(cfg_path)
    store = CandleStore(cfg.root, cfg.interval)
    hist = walk(25, seed=1, vol=0.004)
    days = hist.index.normalize().unique()
    today = days[-1]
    for s in ("AAA", "BBB"):
        store.write(s, hist[hist.index.normalize() < today])
    todays = hist[hist.index.normalize() == today]

    def fetch(symbol, start, end):
        return todays[(todays.index >= start) & (todays.index <= end)]

    noon = today + pd.Timedelta(hours=12, minutes=1)
    paper.poll(cfg, fetch, noon.to_pydatetime())
    assert store.read("AAA").index[-1] == today + pd.Timedelta(hours=11, minutes=55)  # 12:00 still forming
    saved = json.loads(paper.today_path(cfg).read_text())
    assert saved["strategies"] == ["orb"] and saved["day"] == str(today.date())
    assert paper.history(cfg) == []  # nothing stored before the close
    close = today + pd.Timedelta(hours=15, minutes=35)
    final = paper.poll(cfg, fetch, close.to_pydatetime())
    assert all(r["status"] == "closed" for r in final["rows"])
    if any(r["taken"] for r in final["rows"]):
        assert paper.history(cfg)[0]["day"] == str(today.date())
    assert paper.last_polls(cfg)[0]["ok"]


def test_poll_survives_a_failing_stock(cfg_path):
    cfg = cfgmod.load(cfg_path)

    def fetch(symbol, start, end):
        raise RuntimeError("Kite said no")

    result = paper.poll(cfg, fetch, datetime(2026, 1, 5, 10, 0))
    assert len(result["errors"]) == 2 and not paper.last_polls(cfg)[0]["ok"]


def test_dashboard_page_and_saving_strategies(cfg_path, monkeypatch):
    from trademare.intraday import server

    app = server.create_app(str(cfg_path))
    client = TestClient(app)
    page = client.get("/")
    assert page.status_code == 200 and "Intraday" in page.text and "Opening range breakout" in page.text
    assert "No study yet" in page.text
    token = re.search(r'name="token" value="([^"]+)"', page.text).group(1)
    r = client.post("/strategies", data={"token": token, "strategy": ["orb", "nope"], "require_record": "on"},
                    follow_redirects=False)  # fmt: skip
    assert r.status_code == 303 and cfgmod.load(cfg_path).paper.strategies == ["orb"]
    assert client.post("/strategies", data={"token": "forged"}).status_code == 403
    monkeypatch.setattr(server, "scheduled_poll", lambda path=None: "market closed")
    assert (
        "market%20closed"
        in client.post("/poll", data={"token": token}, follow_redirects=False).headers["location"]
    )


def test_scheduled_poll_skips_closed_market(cfg_path):
    from trademare.intraday.server import scheduled_poll

    assert scheduled_poll(str(cfg_path), now=datetime(2026, 11, 1, 11, 0)) == "market closed"  # a Sunday
    assert scheduled_poll(str(cfg_path), now=datetime(2026, 11, 2, 8, 0)) == "market closed"  # before 09:20
