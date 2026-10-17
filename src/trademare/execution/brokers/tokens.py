"""Session tokens for brokers that need a daily login (e.g. Zerodha Kite).

Stored under data/tokens/ (git-ignored) with owner-only permissions.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

TOKEN_DIR = Path("data/tokens")


def save_token(broker: str, access_token: str, day: date | None = None, directory: Path = TOKEN_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    path = directory / f"{broker}.json"
    path.write_text(json.dumps({"access_token": access_token, "date": (day or date.today()).isoformat()}))
    os.chmod(path, 0o600)
    return path


def load_token(broker: str, directory: Path = TOKEN_DIR) -> tuple[str, date] | None:
    path = directory / f"{broker}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return data["access_token"], date.fromisoformat(data["date"])
