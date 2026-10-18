"""Forensic accounting scores from the last two fiscal years of statements.

- Piotroski F-score (2000): nine pass/fail tests of profitability, balance-sheet strength and
  efficiency. 7-9 is strong, 0-3 weak. Tests whose inputs are missing are skipped, so the
  score is reported out of the tests that could run.
- Altman Z''-score (Altman 2005, the non-manufacturer / emerging-market version, which needs no
  market price): below 1.1 is the distress zone, 1.1-2.6 grey, above 2.6 safe.
- Beneish M-score (1999, eight variables): above -1.78 resembles companies that manipulated
  earnings. A screen, not proof.
Income is taken before one-off items where the source separates them (a large one-off gain would
otherwise pass the profitability tests).

- Accruals (Sloan 1996): (net income - operating cash flow) / assets. High accruals mean profits
  that are not turning into cash.

Not meaningful for banks, lenders and insurers (no gross margin, current ratio or comparable
leverage), so Altman and Beneish are skipped for financials. Everything is point in time: only
statements public on ``asof`` are used.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

FINANCIAL_SECTORS = ("financ", "bank", "insur", "nbfc", "lending")
ALTMAN_DISTRESS, ALTMAN_SAFE = 1.1, 2.6
BENEISH_THRESHOLD = -1.78


@dataclass
class Forensics:
    fiscal_year: date | None = None
    piotroski: int | None = None
    piotroski_tests: int = 0
    piotroski_detail: dict[str, bool] = field(default_factory=dict)
    altman_z: float | None = None
    altman_zone: str | None = None
    beneish_m: float | None = None
    beneish_flag: bool | None = None
    accruals: float | None = None
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        d = self.__dict__.copy()
        d["fiscal_year"] = self.fiscal_year.isoformat() if self.fiscal_year else None
        return d


def _v(row: pd.Series, key: str) -> float | None:
    x = row.get(key)
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def _div(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None or b == 0 else a / b


def _income(row: pd.Series) -> float | None:
    """Income before one-off items (Piotroski's "before extraordinary items"), else reported income."""
    normalized = _v(row, "net_income_normalized")
    return normalized if normalized is not None else _v(row, "net_income")


def is_financial(sector: str | None) -> bool:
    return bool(sector) and any(k in sector.lower() for k in FINANCIAL_SECTORS)


PIOTROSKI_LABELS = {
    "roa_positive": "Profitable (return on assets > 0)",
    "cfo_positive": "Positive operating cash flow",
    "roa_improving": "Return on assets improved",
    "cash_beats_profit": "Operating cash flow above net income",
    "leverage_falling": "Debt / assets fell",
    "liquidity_rising": "Current ratio improved",
    "no_dilution": "No new shares issued",
    "margin_rising": "Gross margin improved",
    "turnover_rising": "Asset turnover improved",
}


def compute_forensics(
    financials: pd.DataFrame | None, asof: date, sector: str | None = None
) -> Forensics | None:
    if financials is None or financials.empty:
        return None
    annual = financials[
        (financials["period_type"] == "A") & (financials["available_from"] <= pd.Timestamp(asof))
    ].sort_values("period_end")
    if len(annual) < 2:
        return None
    t, p = annual.iloc[-1], annual.iloc[-2]
    out = Forensics(fiscal_year=pd.Timestamp(t["period_end"]).date())

    # ---- Piotroski (end-of-year assets as denominators, consistently for both years)
    roa_t, roa_p = (
        _div(_income(t), _v(t, "total_assets")),
        _div(_income(p), _v(p, "total_assets")),
    )
    cfo_t, ni_t = _v(t, "operating_cash_flow"), _income(t)
    lev_t = _div(_v(t, "total_debt") or 0.0, _v(t, "total_assets")) if _v(t, "total_assets") else None
    lev_p = _div(_v(p, "total_debt") or 0.0, _v(p, "total_assets")) if _v(p, "total_assets") else None
    cr_t = _div(_v(t, "current_assets"), _v(t, "current_liabilities"))
    cr_p = _div(_v(p, "current_assets"), _v(p, "current_liabilities"))
    gm_t, gm_p = _div(_v(t, "gross_profit"), _v(t, "revenue")), _div(_v(p, "gross_profit"), _v(p, "revenue"))
    at_t, at_p = _div(_v(t, "revenue"), _v(t, "total_assets")), _div(_v(p, "revenue"), _v(p, "total_assets"))
    sh_t, sh_p = _v(t, "shares_outstanding"), _v(p, "shares_outstanding")
    tests: dict[str, bool | None] = {
        "roa_positive": None if roa_t is None else roa_t > 0,
        "cfo_positive": None if cfo_t is None else cfo_t > 0,
        "roa_improving": None if roa_t is None or roa_p is None else roa_t > roa_p,
        "cash_beats_profit": None if cfo_t is None or ni_t is None else cfo_t > ni_t,
        "leverage_falling": None if lev_t is None or lev_p is None else lev_t <= lev_p,
        "liquidity_rising": None if cr_t is None or cr_p is None else cr_t > cr_p,
        "no_dilution": None if sh_t is None or sh_p is None else sh_t <= sh_p * 1.01,
        "margin_rising": None if gm_t is None or gm_p is None else gm_t > gm_p,
        "turnover_rising": None if at_t is None or at_p is None else at_t > at_p,
    }
    out.piotroski_detail = {k: v for k, v in tests.items() if v is not None}
    out.piotroski_tests = len(out.piotroski_detail)
    if out.piotroski_tests >= 6:
        out.piotroski = sum(out.piotroski_detail.values())
    else:
        out.notes.append(f"Piotroski: only {out.piotroski_tests} of 9 tests have data")

    ta = _v(t, "total_assets")
    if cfo_t is not None and ni_t is not None and ta:
        out.accruals = (ni_t - cfo_t) / ta

    if is_financial(sector):
        out.notes.append("Altman and Beneish are not meaningful for financial companies")
        return out

    # ---- Altman Z'' = 6.56 WC/TA + 3.26 RE/TA + 6.72 EBIT/TA + 1.05 BookEquity/TL
    wc = (
        None
        if _v(t, "current_assets") is None or _v(t, "current_liabilities") is None
        else (_v(t, "current_assets") - _v(t, "current_liabilities"))
    )
    parts = [_div(wc, ta), _div(_v(t, "retained_earnings"), ta), _div(_v(t, "operating_income"), ta),
             _div(_v(t, "total_equity"), _v(t, "total_liabilities"))]  # fmt: skip
    if all(x is not None for x in parts):
        z = 6.56 * parts[0] + 3.26 * parts[1] + 6.72 * parts[2] + 1.05 * parts[3]
        out.altman_z = round(z, 2)
        out.altman_zone = "distress" if z < ALTMAN_DISTRESS else "grey" if z < ALTMAN_SAFE else "safe"
    else:
        out.notes.append("Altman: missing working capital, retained earnings, EBIT or liabilities")

    # ---- Beneish M (8 variables)
    def ratio(a: float | None, b: float | None) -> float | None:
        return _div(a, b)

    sales_t, sales_p = _v(t, "revenue"), _v(p, "revenue")
    dep_t, dep_p, ppe_t, ppe_p = _v(t, "depreciation"), _v(p, "depreciation"), _v(t, "ppe"), _v(p, "ppe")
    dsri = ratio(_div(_v(t, "receivables"), sales_t), _div(_v(p, "receivables"), sales_p))
    gmi = ratio(gm_p, gm_t)

    def aq(row):
        return (
            None
            if any(_v(row, k) is None for k in ("current_assets", "ppe")) or not _v(row, "total_assets")
            else (1 - (_v(row, "current_assets") + _v(row, "ppe")) / _v(row, "total_assets"))
        )

    aqi = ratio(aq(t), aq(p))
    sgi = ratio(sales_t, sales_p)
    depi = None
    if None not in (dep_t, dep_p, ppe_t, ppe_p) and dep_t + ppe_t and dep_p + ppe_p:
        depi = ratio(dep_p / (dep_p + ppe_p), dep_t / (dep_t + ppe_t))
    sgai = ratio(_div(_v(t, "sga"), sales_t), _div(_v(p, "sga"), sales_p))
    lvgi = ratio(
        _div(_v(t, "total_liabilities"), ta), _div(_v(p, "total_liabilities"), _v(p, "total_assets"))
    )
    tata = out.accruals
    variables = (dsri, gmi, aqi, sgi, depi, sgai, tata, lvgi)
    if all(x is not None for x in variables):
        m = (-4.84 + 0.920 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi + 0.115 * depi
             - 0.172 * sgai + 4.679 * tata - 0.327 * lvgi)  # fmt: skip
        out.beneish_m = round(m, 2)
        out.beneish_flag = m > BENEISH_THRESHOLD
    else:
        missing = [
            n
            for n, x in zip(
                ("DSRI", "GMI", "AQI", "SGI", "DEPI", "SGAI", "TATA", "LVGI"), variables, strict=True
            )
            if x is None
        ]
        out.notes.append(f"Beneish: missing {', '.join(missing)}")
    return out
