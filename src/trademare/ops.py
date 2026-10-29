"""Operational safety: nightly database backups and a missed-run alarm.

- ``backup_database`` copies the SQLite database with SQLite's online-backup API (safe while the
  dashboard and scheduler are writing) to ``ops.backup_dir/trademare-YYYY-MM-DD.db`` and keeps the
  newest ``ops.backup_keep`` copies.
- ``check_health`` lists problems: a market whose daily run is overdue (the server was down, the
  Mac slept through it, or the run failed or hung), a tripped kill switch, or a stale backup.
- ``alert_health`` sends those problems by Telegram and email, at most once a day for the same
  problems, so a long outage does not spam you.

``trademare serve`` runs both on a schedule. Because a dead server cannot report its own death,
``deploy/macos/install.sh`` also installs a separate launchd job that runs ``trademare health``
every two hours.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from trademare.config.schema import AppConfig
from trademare.config.secrets import Secrets
from trademare.core.clock import MarketClock
from trademare.storage.db import Database, MarketStateRow

log = logging.getLogger(__name__)

BACKUP_PREFIX = "trademare-"
BACKUP_STALE_DAYS = 2


def sqlite_path(database_url: str) -> Path | None:
    if not database_url.startswith("sqlite:///") or database_url == "sqlite:///:memory:":
        return None
    return Path(database_url.removeprefix("sqlite:///"))


def list_backups(backup_dir: str | Path) -> list[Path]:
    """Backups, oldest first."""
    d = Path(backup_dir)
    return sorted(d.glob(f"{BACKUP_PREFIX}*.db")) if d.exists() else []


def backup_database(config: AppConfig, today: date | None = None) -> Path:
    src = sqlite_path(config.database_url)
    if src is None:
        raise ValueError(f"backups support SQLite files only, not {config.database_url}")
    if not src.exists():
        raise FileNotFoundError(src)
    out_dir = Path(config.ops.backup_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{BACKUP_PREFIX}{(today or date.today()).isoformat()}.db"
    tmp = dest.with_suffix(".db.part")
    with sqlite3.connect(src) as source, sqlite3.connect(tmp) as target:
        source.backup(target)
    tmp.replace(dest)  # a half-written copy never looks like a good backup
    for old in list_backups(out_dir)[: -config.ops.backup_keep]:
        old.unlink()
    log.info("database backed up to %s", dest)
    return dest


def restore_database(config: AppConfig, backup: Path) -> Path:
    """Replace the live database with ``backup``; the current one is kept alongside as *.before-restore."""
    dest = sqlite_path(config.database_url)
    if dest is None:
        raise ValueError(f"restore supports SQLite files only, not {config.database_url}")
    if dest.exists():
        keep = dest.with_suffix(f".before-restore-{datetime.now():%Y%m%d-%H%M%S}.db")
        with sqlite3.connect(dest) as source, sqlite3.connect(keep) as target:
            source.backup(target)
    with sqlite3.connect(backup) as source, sqlite3.connect(dest) as target:
        source.backup(target)
    return dest


def _run_at(clock: MarketClock, session: date, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.combine(session, time(h, m), ZoneInfo(clock.tz))


def expected_session(clock: MarketClock, run_time: str, grace: timedelta, now: datetime) -> date:
    """The latest session whose daily run should have finished by ``now``."""
    session = clock.last_completed_session(now)
    while _run_at(clock, session, run_time) + grace > now:
        session = clock.previous_session(session)
    return session


@dataclass
class HealthReport:
    problems: list[str]
    checked: list[str]  # what looked fine, for the CLI

    @property
    def ok(self) -> bool:
        return not self.problems


def check_health(config: AppConfig, now: datetime | None = None, db: Database | None = None) -> HealthReport:
    now = now or datetime.now(ZoneInfo("UTC"))
    db = db or Database(config.database_url)
    grace = timedelta(hours=config.ops.health_grace_hours)
    problems: list[str] = []
    checked: list[str] = []
    for market, cfg in config.enabled_markets().items():
        clock = MarketClock(market)
        due = expected_session(clock, cfg.daily_run_time, grace, now)
        with db.session() as s:
            state = s.get(MarketStateRow, market.value)
            last = state.last_processed if state else None
            kill = state.kill_reason if state and state.kill_switch else None
        if last is None:
            checked.append(f"{market}: no daily run yet (first run pending)")
        elif last < due:
            missed = len(clock.sessions_between(last, due))
            problems.append(
                f"{market}: the daily run for {due} has not happened (last processed {last}, "
                f"{missed} session{'s' if missed != 1 else ''} behind). Is `trademare serve` running and "
                "the computer awake? Stops already at the broker still stand."
            )
        else:
            checked.append(f"{market}: processed through {last}")
        if kill:
            problems.append(
                f"{market}: kill switch is tripped ({kill}). New entries stay blocked until re-armed."
            )

    if config.ops.backup_enabled and sqlite_path(config.database_url):
        backups = list_backups(config.ops.backup_dir)
        if not backups:
            checked.append("backups: none yet (the first runs tonight)")
        else:
            newest = backups[-1]
            age = now - datetime.fromtimestamp(newest.stat().st_mtime, ZoneInfo("UTC"))
            if age > timedelta(days=BACKUP_STALE_DAYS):
                problems.append(f"backups: the newest ({newest.name}) is {age.days} days old")
            else:
                checked.append(f"backups: {len(backups)} kept, newest {newest.name}")
    return HealthReport(problems, checked)


def _state_path(config: AppConfig) -> Path:
    return Path(config.data.cache_dir).parent / "health_state.json"


def alert_health(config: AppConfig, secrets: Secrets, report: HealthReport, today: date | None = None) -> str:
    """Send the problems once per day per distinct set; returns what happened."""
    if report.ok:
        return "healthy"
    today = today or date.today()
    digest = hashlib.sha256("\n".join(sorted(report.problems)).encode()).hexdigest()[:16]
    path = _state_path(config)
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        state = {}
    if state.get("digest") == digest and state.get("day") == today.isoformat():
        return "already alerted today"

    from trademare.notify.email import send_text_email
    from trademare.notify.phone import send_text

    body = "TradeMare health check found a problem:\n\n" + "\n\n".join(f"• {p}" for p in report.problems)
    sent = [
        send_text(config, secrets, body),
        send_text_email(config, secrets, "TradeMare health alert", body),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"digest": digest, "day": today.isoformat()}))
    return "; ".join(sent)
