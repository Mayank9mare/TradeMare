"""NSE surveillance lists: stocks under ASM (additional surveillance) or GSM (graded surveillance).

Stocks on these lists have unusual price or volume behaviour and can face higher margins, trade-for-
trade settlement or price bands; TradeMare does not open new positions in them. The lists come
from NSE's public site once a day and are cached; failures return an empty list with an error, so
a missing list never blocks a protective exit (only new buys consult it).
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

NSE_ASM = "https://www.nseindia.com/api/reportASM"
NSE_GSM = "https://www.nseindia.com/api/reportGSM"


def parse_asm(payload: dict) -> dict[str, str]:
    out = {}
    for part in ("longterm", "shortterm"):
        for r in (payload.get(part) or {}).get("data") or []:
            if r.get("symbol"):
                out[str(r["symbol"]).strip()] = str(r.get("survDesc") or r.get("survCode") or "ASM")
    return out


def parse_gsm(payload: list) -> dict[str, str]:
    return {
        str(r["symbol"]).strip(): str(r.get("survDesc") or r.get("survCode") or "GSM")
        for r in payload or []
        if r.get("symbol")
    }


class SurveillanceService:
    def __init__(self, cache_dir: str | Path, fetch=None) -> None:
        self.dir = Path(cache_dir) / "surveillance"
        self._fetch = fetch  # url -> payload, for tests
        self.error: str | None = None

    def _get(self, url: str):
        if self._fetch is not None:
            return self._fetch(url)
        from trademare.data.events import NseClient

        return NseClient(pause=1.0).get_json(url)

    def flagged(self, today: date | None = None) -> dict[str, str]:
        """{symbol: why} for every stock on an ASM or GSM list today."""
        today = today or date.today()
        path = self.dir / f"{today.isoformat()}.json"
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            pass
        try:
            flagged = parse_asm(self._get(NSE_ASM)) | parse_gsm(self._get(NSE_GSM))
        except Exception as e:  # network or format: the check is skipped, not failed
            self.error = f"surveillance lists unavailable: {e}"
            log.warning(self.error)
            return {}
        self.dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(flagged))
        return flagged
