import os
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from trademare.config import load_config
from trademare.core.clock import MarketClock
from trademare.core.models import Market
from trademare.ops import (
    alert_health,
    backup_database,
    check_health,
    expected_session,
    list_backups,
    restore_database,
)
from trademare.storage.db import Database, MarketStateRow

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def config(tmp_path, base_config_dict, write_config):
    d = base_config_dict
    d["database_url"] = f"sqlite:///{tmp_path}/t.db"
    d["data"]["cache_dir"] = str(tmp_path / "cache")
    d["ops"]["backup_dir"] = str(tmp_path / "backups")
    d["ops"]["backup_keep"] = 2
    d["markets"]["US"]["enabled"] = False
    return load_config(write_config(d))


def set_last(db: Database, day: date) -> None:
    with db.session() as s:
        db.market_state(s, "IN", 100_000).last_processed = day


def test_backup_copies_and_prunes(config):
    db = Database(config.database_url)
    set_last(db, date(2025, 6, 2))
    for day in (date(2025, 6, 1), date(2025, 6, 2), date(2025, 6, 3)):
        backup_database(config, today=day)
    backups = list_backups(config.ops.backup_dir)
    assert [b.name for b in backups] == ["trademare-2025-06-02.db", "trademare-2025-06-03.db"]
    with sqlite3.connect(backups[-1]) as c:
        assert c.execute("select last_processed from market_state").fetchone()[0] == "2025-06-02"


def test_restore_replaces_live_db_and_keeps_previous(config, tmp_path):
    db = Database(config.database_url)
    set_last(db, date(2025, 6, 2))
    backup = backup_database(config, today=date(2025, 6, 2))
    set_last(db, date(2025, 6, 9))
    restore_database(config, backup)
    with Database(config.database_url).session() as s:
        assert s.get(MarketStateRow, "IN").last_processed == date(2025, 6, 2)
    assert list(tmp_path.glob("t.before-restore-*.db"))


def test_expected_session_waits_for_run_time_plus_grace():
    clock, grace = MarketClock(Market.IN), timedelta(hours=3)
    monday = date(2025, 6, 2)
    assert expected_session(clock, "17:00", grace, datetime(2025, 6, 2, 21, 0, tzinfo=IST)) == monday
    assert expected_session(clock, "17:00", grace, datetime(2025, 6, 2, 19, 0, tzinfo=IST)) == date(
        2025, 5, 30
    )
    assert expected_session(clock, "17:00", grace, datetime(2025, 6, 1, 12, 0, tzinfo=IST)) == date(
        2025, 5, 30
    )


def test_health_flags_missed_run_and_alerts_once_a_day(config, monkeypatch):
    db = Database(config.database_url)
    now = datetime(2025, 6, 3, 21, 0, tzinfo=IST)
    set_last(db, date(2025, 6, 3))
    backup_database(config)
    assert check_health(config, now=now, db=db).ok

    set_last(db, date(2025, 5, 30))
    report = check_health(config, now=now, db=db)
    assert not report.ok and "2 sessions behind" in report.problems[0]

    sent = []
    monkeypatch.setattr("trademare.notify.telegram.send_text", lambda c, s, text: sent.append(text) or "tg")
    monkeypatch.setattr("trademare.notify.email.send_text_email", lambda c, s, subj, body: "mail")
    assert alert_health(config, None, report, today=date(2025, 6, 3)) == "tg; mail"
    assert alert_health(config, None, report, today=date(2025, 6, 3)) == "already alerted today"
    assert alert_health(config, None, report, today=date(2025, 6, 4)) == "tg; mail"
    assert len(sent) == 2 and "2 sessions behind" in sent[0]


def test_health_flags_stale_backup_and_kill_switch(config):
    db = Database(config.database_url)
    now = datetime.now(IST)
    set_last(db, expected_session(MarketClock(Market.IN), "17:00", timedelta(hours=3), now))
    with db.session() as s:
        st = db.market_state(s, "IN", 100_000)
        st.kill_switch, st.kill_reason = True, "daily loss 3.2%"
    path = backup_database(config)
    old = (now - timedelta(days=5)).timestamp()
    os.utime(path, (old, old))
    problems = check_health(config, now=now, db=db).problems
    assert len(problems) == 2
    assert "kill switch" in problems[0] and "5 days old" in problems[1]
