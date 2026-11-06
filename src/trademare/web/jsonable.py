"""Turn a classic page's template context into JSON for the React app, so both UIs read the
same data: dataclasses, pydantic models, SQLAlchemy rows, dates, enums, numpy/pandas values."""

from __future__ import annotations

import dataclasses
import enum
import math
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

MAX_DEPTH = 12


def to_jsonable(obj: Any, depth: int = 0) -> Any:
    if depth > MAX_DEPTH:
        return None
    nxt = depth + 1
    if obj is None or isinstance(obj, (bool, str, int)):
        return obj
    if isinstance(obj, float):
        return None if math.isnan(obj) or math.isinf(obj) else obj
    if isinstance(obj, enum.Enum):
        return to_jsonable(obj.value, nxt)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {str(to_jsonable(k, nxt)): to_jsonable(v, nxt) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [to_jsonable(v, nxt) for v in obj]
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        out = {f.name: to_jsonable(getattr(obj, f.name), nxt) for f in dataclasses.fields(obj)}
        return _with_extras(obj, out, nxt)
    if hasattr(obj, "model_dump"):  # pydantic
        return to_jsonable(obj.model_dump(mode="json"), nxt)
    table = getattr(obj, "__table__", None)
    if table is not None:  # SQLAlchemy row: its columns plus attributes a route attached
        out = {c.key: to_jsonable(getattr(obj, c.key, None), nxt) for c in table.columns}
        return _with_extras(obj, out, nxt)
    try:  # numpy / pandas scalars and timestamps
        import numpy as np
        import pandas as pd

        if isinstance(obj, pd.Timestamp):
            return None if pd.isna(obj) else obj.isoformat()
        if isinstance(obj, np.generic):
            return to_jsonable(obj.item(), nxt)
        if isinstance(obj, pd.Series):
            return to_jsonable(obj.to_dict(), nxt)
        if isinstance(obj, pd.DataFrame):
            return to_jsonable(obj.to_dict(orient="records"), nxt)
    except ImportError:  # pragma: no cover
        pass
    if hasattr(obj, "__dict__"):
        return {k: to_jsonable(v, nxt) for k, v in vars(obj).items() if not k.startswith("_")}
    return str(obj)


def _with_extras(obj: Any, out: dict, depth: int) -> dict:
    """Attributes a route set on a row or dataclass instance (e.g. ``sig.is_expired``), and a
    dataclass's read-only properties (e.g. an IPO card's ``listing_gain``) that templates read."""
    for k, v in getattr(obj, "__dict__", {}).items():
        if not k.startswith("_") and k not in out:
            out[k] = to_jsonable(v, depth)
    if dataclasses.is_dataclass(obj):
        for cls in type(obj).__mro__:
            for k, attr in vars(cls).items():
                if isinstance(attr, property) and not k.startswith("_") and k not in out:
                    try:
                        out[k] = to_jsonable(getattr(obj, k), depth)
                    except Exception:  # a property that needs data we do not have: leave it out
                        continue
    return out
