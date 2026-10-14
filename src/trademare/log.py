"""Logging setup: console plus a rotating file under logs/."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(level: str = "INFO", log_dir: Path | None = Path("logs")) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(log_dir / "trademare.log", maxBytes=5_000_000, backupCount=5))
    logging.basicConfig(level=level.upper(), format=FORMAT, handlers=handlers, force=True)
