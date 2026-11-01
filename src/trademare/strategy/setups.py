"""Chart setups from "51 Trading Strategies" (Aseem Singhal, 2023), as exact daily rules.

Each setup turns a symbol's daily bars into a frame with one row per day:

- ``signal``: all of the setup's conditions hold on that close;
- ``stop``: where the book puts the stop for a trade from that signal;
- ``target``: a price target from the chart (NaN if the setup uses an R multiple or none);
- ``trail``: a level the stop trails up to while the trade is open (NaN = no trailing);
- ``exit``: the setup's own exit condition holds on that close;
- ``strength``: how strongly the signal fired, for ranking several signals on one day.

The entry is a buy-stop at the signal day's high, valid the next session (the book's "buy above
the high of the candle"). All columns are causal: the value on day t uses bars up to t only.
Thresholds come from docs/plans/2026-11-01-book-setups-plan.md and were fixed before testing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from trademare.analysis import indicators as ind

MAX_HOLD = 60  # sessions; every setup trade exits after this if nothing else closed it
MAX_RISK_PCT = 0.25  # a stop more than 25% below the entry is treated as no trade


class SetupContext:
    """Universe-wide inputs some setups need (sector strength, the benchmark).

    Built once from every symbol's bars; setups that need nothing from it ignore it.
    """

    def __init__(
        self,
        bars: dict[str, pd.DataFrame] | None = None,
        sectors: dict[str, str] | None = None,
        benchmark: pd.DataFrame | None = None,
    ) -> None:
        self.bars = bars or {}
        self.sectors = sectors or {}
        self.benchmark = benchmark
        self._sector: tuple[pd.DataFrame, pd.DataFrame] | None = None

    def sector_strength(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """(rs, leader): each sector's 63-session return minus the benchmark's, and its most-traded
        member (126-session average traded value), both indexed by date with sectors as columns."""
        if self._sector is None:
            rs_cols, leader_cols = {}, {}
            if self.benchmark is not None and self.bars:
                closes = pd.DataFrame({s: b["close"] for s, b in self.bars.items()})
                value = pd.DataFrame(
                    {
                        s: (b["close"] * b["volume"]).rolling(126, min_periods=60).mean()
                        for s, b in self.bars.items()
                    }
                )
                bench = self.benchmark["close"].reindex(closes.index).ffill()
                bench_ret = bench / bench.shift(63) - 1
                for sector in sorted({self.sectors.get(s, "") for s in closes.columns} - {""}):
                    members = [s for s in closes.columns if self.sectors.get(s) == sector]
                    if len(members) < 3:
                        continue
                    daily = closes[members].pct_change(fill_method=None).mean(axis=1, skipna=True)
                    index = (1 + daily.fillna(0)).cumprod()
                    rs_cols[sector] = index / index.shift(63) - 1 - bench_ret
                    sized = value[members]
                    leader_cols[sector] = sized.fillna(-1.0).idxmax(axis=1).where(sized.notna().any(axis=1))
            self._sector = (pd.DataFrame(rs_cols), pd.DataFrame(leader_cols))
        return self._sector


@dataclass(frozen=True)
class Setup:
    id: str
    name: str
    book: str  # section in the book, e.g. "4.2"
    family: str  # Trend | Breakout | Pullback | Reversal
    summary: str  # the rules in one or two plain sentences
    rule: Callable[[pd.DataFrame, SetupContext, str], pd.DataFrame] = field(repr=False)
    target_r: float | None = None  # target at this multiple of the risk (max with a chart target)
    exit_text: str = ""
    max_hold: int = MAX_HOLD


# ---------------------------------------------------------------- helpers
def _frame(bars: pd.DataFrame) -> pd.DataFrame:
    f = pd.DataFrame(index=bars.index)
    f["signal"] = False
    f["stop"] = np.nan
    f["target"] = np.nan
    f["trail"] = np.nan
    f["exit"] = False
    f["strength"] = 0.0
    return f


def _vol50(bars: pd.DataFrame) -> pd.Series:
    return bars["volume"].shift(1).rolling(50, min_periods=50).mean()


def _windows(series: pd.Series, n: int) -> np.ndarray:
    """(len, n) array of the trailing n values ending at each row (NaN-padded at the start)."""
    values = series.to_numpy(dtype=float)
    padded = np.concatenate([np.full(n - 1, np.nan), values])
    return np.lib.stride_tricks.sliding_window_view(padded, n)


def _rolling_slope(series: pd.Series, n: int) -> pd.Series:
    """Least-squares slope over the trailing n values, per step."""
    x = np.arange(n) - (n - 1) / 2
    w = _windows(series, n)
    slope = (w - np.nanmean(w, axis=1, keepdims=True)) @ x / (x @ x)
    return pd.Series(slope, index=series.index)


def _age_of_max(series: pd.Series, n: int) -> pd.Series:
    """Sessions since the highest value of the trailing n (0 = today)."""
    w = np.nan_to_num(_windows(series, n), nan=-np.inf)
    return pd.Series(n - 1 - w.argmax(axis=1), index=series.index)


def _age_of_min(series: pd.Series, n: int) -> pd.Series:
    w = np.nan_to_num(_windows(series, n), nan=np.inf)
    return pd.Series(n - 1 - w.argmin(axis=1), index=series.index)


def _crossed_above(a: pd.Series, level: pd.Series | float) -> pd.Series:
    prev = level.shift() if isinstance(level, pd.Series) else level
    return (a > level) & (a.shift() <= prev)


# ---------------------------------------------------------------- trend
def _supertrend_rsi(bars: pd.DataFrame, ctx: SetupContext, symbol: str) -> pd.DataFrame:
    f = _frame(bars)
    st = ind.supertrend(bars)
    r = ind.rsi(bars["close"])
    both = st["up"] & (bars["close"] > st["line"]) & (r > 60)
    f["signal"] = both & ~both.shift(fill_value=False)  # first day both hold, in either order
    f["stop"] = f["trail"] = st["line"].where(st["up"])
    f["exit"] = ~st["up"] & st["line"].notna()
    f["strength"] = r
    return f


def _ichimoku(bars: pd.DataFrame, ctx: SetupContext, symbol: str) -> pd.DataFrame:
    f = _frame(bars)
    ich = ind.ichimoku(bars)
    close = bars["close"]
    both = (ich["conversion"] > ich["base"]) & (close > ich["cloud_top"])
    f["signal"] = both & ~both.shift(fill_value=False)
    f["stop"] = ich["span_a"]
    f["strength"] = ich["conversion"] / ich["base"] - 1
    return f


def _sector_rs(bars: pd.DataFrame, ctx: SetupContext, symbol: str) -> pd.DataFrame:
    f = _frame(bars)
    sector = ctx.sectors.get(symbol)
    rs, leader = ctx.sector_strength()
    if sector is None or sector not in rs:
        return f
    srs = rs[sector].reindex(bars.index)
    is_leader = leader[sector].reindex(bars.index) == symbol
    f["signal"] = (srs > 0) & (srs.shift() <= 0) & is_leader
    f["stop"] = bars["close"] - 3 * ind.atr(bars)
    f["exit"] = srs < 0
    f["strength"] = srs
    return f


# ---------------------------------------------------------------- breakouts
def _rally_base_breakout(bars: pd.DataFrame, ctx: SetupContext, symbol: str) -> pd.DataFrame:
    f = _frame(bars)
    o, h, lo, c, v = (bars[k] for k in ("open", "high", "low", "close", "volume"))
    base_high = h.shift(1).rolling(20, min_periods=20).max()
    base_low = lo.shift(1).rolling(20, min_periods=20).min()
    rally = c.shift(21) / lo.shift(21).rolling(60, min_periods=60).min() - 1
    pre_volume = v.shift(21).rolling(50, min_periods=50).mean()
    quiet = v.shift(1).rolling(20, min_periods=20).mean() < 0.8 * pre_volume
    solid = (c > o) & ((c - o) >= 0.6 * (h - lo))
    f["signal"] = (
        (base_high / base_low - 1 <= 0.15)
        & (rally >= 0.15)
        & quiet
        & (c > base_high)
        & (v >= 1.5 * _vol50(bars))
        & solid
    )
    f["stop"] = np.minimum(base_low, h * 0.95)
    f["strength"] = v / _vol50(bars)
    return f


def _vcp(bars: pd.DataFrame, ctx: SetupContext, symbol: str) -> pd.DataFrame:
    f = _frame(bars)
    h, lo, c, v = (bars[k] for k in ("high", "low", "close", "volume"))

    def rng(shift: int) -> pd.Series:
        return (
            h.shift(shift).rolling(15, min_periods=15).max()
            / lo.shift(shift).rolling(15, min_periods=15).min()
            - 1
        )

    r1, r2, r3 = rng(31), rng(16), rng(1)
    v1 = v.shift(31).rolling(15, min_periods=15).mean()
    v3 = v.shift(1).rolling(15, min_periods=15).mean()
    sma50, sma200 = ind.sma(c, 50), ind.sma(c, 200)
    f["signal"] = (
        (c > sma50)
        & (sma50 > sma200)
        & (r1 > r2)
        & (r2 > r3)
        & (r3 <= 0.10)
        & (v3 < v1)
        & (c > h.shift(1).rolling(45, min_periods=45).max())
        & (v >= 1.5 * _vol50(bars))
    )
    f["stop"] = lo.shift(1).rolling(15, min_periods=15).min()
    f["strength"] = r1 / r3.replace(0, np.nan)
    return f


def _bb_squeeze(bars: pd.DataFrame, ctx: SetupContext, symbol: str) -> pd.DataFrame:
    f = _frame(bars)
    bb = ind.bollinger(bars["close"])
    pct = bb["width"].rolling(126, min_periods=126).rank(pct=True)
    squeezed = (pct <= 0.2).astype(float).shift(1).rolling(5, min_periods=5).max() == 1
    f["signal"] = squeezed & (bars["close"] > bb["upper"]) & (bb["width"] > bb["width"].shift())
    f["stop"] = f["trail"] = bb["mid"]
    f["exit"] = bars["close"] < bb["mid"]
    f["strength"] = bb["width"] / bb["width"].shift() - 1
    return f


def _triangle_breakout(bars: pd.DataFrame, ctx: SetupContext, symbol: str) -> pd.DataFrame:
    f = _frame(bars)
    h, lo, c, v = (bars[k] for k in ("high", "low", "close", "volume"))
    n = 15
    highs_down = _rolling_slope(h, n).shift(1) < 0
    lows_up = _rolling_slope(lo, n).shift(1) > 0
    volume_down = _rolling_slope(v, n).shift(1) < 0
    window_high = h.shift(1).rolling(n, min_periods=n).max()
    f["signal"] = (
        (c > ind.sma(c, 50))
        & highs_down
        & lows_up
        & volume_down
        & (c > window_high)
        & (v >= 1.5 * _vol50(bars))
    )
    f["stop"] = lo.shift(1).rolling(n, min_periods=n).min()
    f["strength"] = v / _vol50(bars)
    return f


# ---------------------------------------------------------------- pullbacks
def _breakout_retest(bars: pd.DataFrame, ctx: SetupContext, symbol: str) -> pd.DataFrame:
    f = _frame(bars)
    h, lo, c = (bars[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    prior_high = bars["high"].shift(1).rolling(40, min_periods=40).max().to_numpy()
    m = len(c)
    signal = np.zeros(m, dtype=bool)
    stop = np.full(m, np.nan)
    strength = np.zeros(m)
    level, since = np.nan, -1
    touched, broken, lowest = False, False, np.inf
    for i in range(m):
        active = since >= 0 and not broken and i - since <= 15
        if not active and not np.isnan(prior_high[i]) and c[i] > prior_high[i]:
            level, since, touched, broken, lowest = prior_high[i], i, False, False, np.inf
            continue
        if since < 0:
            continue
        lowest = min(lowest, lo[i])
        touched = touched or lo[i] <= level * 1.02
        broken = broken or c[i] < level * 0.98
        age = i - since
        if 3 <= age <= 15 and touched and not broken and i > 0 and c[i] > h[i - 1]:
            signal[i], stop[i] = True, lowest
            strength[i] = c[i] / level - 1
    f["signal"], f["stop"], f["strength"] = signal, stop, strength
    return f


def _williams_pullback(bars: pd.DataFrame, ctx: SetupContext, symbol: str) -> pd.DataFrame:
    f = _frame(bars)
    wr = ind.williams_r(bars)
    overbought = (wr > -20).astype(float).shift(1).rolling(10, min_periods=10).max() == 1
    f["signal"] = (bars["close"] > ind.sma(bars["close"], 14)) & overbought & (wr.shift() <= -50) & (wr > -50)
    f["stop"] = bars["low"].rolling(10, min_periods=10).min()
    f["strength"] = wr
    return f


def _macd_fib(bars: pd.DataFrame, ctx: SetupContext, symbol: str) -> pd.DataFrame:
    f = _frame(bars)
    o, h, lo, c = (bars[k] for k in ("open", "high", "low", "close"))
    swing_high = h.rolling(20, min_periods=20).max()
    swing_low = lo.rolling(60, min_periods=60).min()
    pull_low = lo.rolling(10, min_periods=10).min()
    depth = (swing_high - pull_low) / (swing_high - swing_low)
    high_before_pullback = _age_of_max(h, 20) > _age_of_min(lo, 10)
    macd = ind.macd(c)
    f["signal"] = (
        (c > ind.sma(c, 50))
        & (swing_high / swing_low >= 1.10)
        & depth.between(0.382, 0.618)
        & high_before_pullback
        & _crossed_above(macd["macd"], macd["signal"])
        & (c > o)
    )
    f["stop"] = pull_low
    f["target"] = swing_high
    f["strength"] = macd["hist"] / c
    return f


# ---------------------------------------------------------------- reversals
def _rsi_w(bars: pd.DataFrame, ctx: SetupContext, symbol: str) -> pd.DataFrame:
    f = _frame(bars)
    r = ind.rsi(bars["close"]).to_numpy()
    lo = bars["low"].to_numpy(dtype=float)
    m = len(r)
    signal = np.zeros(m, dtype=bool)
    stop = np.full(m, np.nan)
    strength = np.zeros(m)
    for i in range(1, m):
        if not (r[i] > 30 >= r[i - 1]):
            continue
        start = max(0, i - 25)
        below = r[start:i] <= 30
        runs, j = [], 0  # [start, end) of each below-30 run inside the window
        while j < len(below):
            if below[j]:
                k = j
                while k < len(below) and below[k]:
                    k += 1
                runs.append((start + j, start + k))
                j = k
            else:
                j += 1
        if len(runs) < 2 or runs[-1][1] != i:
            continue
        (a0, a1), (b0, b1) = runs[-2], runs[-1]
        first, second = np.nanmin(r[a0:a1]), np.nanmin(r[b0:b1])
        if second > first:
            signal[i], stop[i], strength[i] = True, np.nanmin(lo[a0 : i + 1]), second - first
    f["signal"], f["stop"], f["strength"] = signal, stop, strength
    f["exit"] = ind.rsi(bars["close"]) >= 70
    return f


def _pin_bar_range(bars: pd.DataFrame, ctx: SetupContext, symbol: str) -> pd.DataFrame:
    f = _frame(bars)
    o, h, lo, c = (bars[k] for k in ("open", "high", "low", "close"))
    range_high = h.shift(1).rolling(20, min_periods=20).max()
    range_low = lo.shift(1).rolling(20, min_periods=20).min()
    rally = c.shift(21) / lo.shift(21).rolling(60, min_periods=60).min() - 1
    body = (c - o).abs()
    lower_wick = np.minimum(o, c) - lo
    upper_wick = h - np.maximum(o, c)
    span = (h - lo).replace(0, np.nan)
    pin = (
        (lower_wick >= 2 * body)
        & (lower_wick >= 0.5 * span)
        & (upper_wick <= body)
        & (c >= lo + 2 * span / 3)
    )
    at_floor = (lo / range_low - 1).abs() <= 0.015
    candidate = pin & at_floor
    earlier = candidate.astype(float).shift(1).rolling(20, min_periods=1).sum()
    f["signal"] = (range_high / range_low - 1 <= 0.10) & (rally >= 0.15) & candidate & (earlier <= 1)
    f["stop"] = lo
    f["target"] = range_high
    f["strength"] = lower_wick / span
    return f


SETUPS: dict[str, Setup] = {
    s.id: s
    for s in [
        Setup(
            "supertrend_rsi",
            "Supertrend + RSI",
            "4.2",
            "Trend",
            "Price above the Supertrend line and RSI crossing above 60 (out of its 40-60 sideways band).",
            _supertrend_rsi,
            exit_text="Stop trails the Supertrend; exit on a close below it.",
        ),
        Setup(
            "ichimoku",
            "Ichimoku cloud",
            "1.7",
            "Trend",
            "Conversion line above the base line and the price closing above the cloud.",
            _ichimoku,
            target_r=3.0,
            exit_text="Stop at the cloud's leading span A; target 3x the risk.",
        ),
        Setup(
            "sector_rs",
            "Sector relative strength",
            "4.3",
            "Trend",
            "A sector starts beating the Nifty over 3 months; buy its most-traded stock.",
            _sector_rs,
            exit_text="Stop 3 ATR below; exit when the sector falls back behind the Nifty.",
        ),
        Setup(
            "rally_base_breakout",
            "Rally, quiet base, breakout",
            "7.2",
            "Breakout",
            "After a 15%+ rally the stock moves sideways on low volume, then breaks out on a big green "
            "candle with heavy volume.",
            _rally_base_breakout,
            target_r=3.0,
            exit_text="Stop at the base low (at least 5% below); target 3x the risk.",
        ),
        Setup(
            "vcp",
            "Volatility contraction (VCP)",
            "7.6",
            "Breakout",
            "In an uptrend, each pullback is smaller than the last on falling volume; buy the breakout "
            "on heavy volume.",
            _vcp,
            target_r=3.0,
            exit_text="Stop under the last contraction; target 3x the risk.",
        ),
        Setup(
            "bb_squeeze",
            "Bollinger squeeze breakout",
            "1.6",
            "Breakout",
            "The Bollinger Bands tighten, then the price closes above the upper band as they widen.",
            _bb_squeeze,
            exit_text="Stop trails the middle band; exit on a close below it.",
        ),
        Setup(
            "triangle_breakout",
            "Triangle breakout",
            "1.4",
            "Breakout",
            "In an uptrend, lower highs and higher lows on falling volume, then a high-volume breakout.",
            _triangle_breakout,
            target_r=2.0,
            exit_text="Stop at the triangle's low; target 2x the risk.",
        ),
        Setup(
            "breakout_retest",
            "Breakout and retest",
            "7.4",
            "Pullback",
            "A breakout above the 40-day high, a pullback that retests the breakout level, then a "
            "close above the previous day's high.",
            _breakout_retest,
            target_r=3.0,
            exit_text="Stop at the retest low; target 3x the risk.",
        ),
        Setup(
            "williams_pullback",
            "Williams %R pullback",
            "1.2",
            "Pullback",
            "Above the 14-day average, %R was overbought, dipped to -50 and turns back up.",
            _williams_pullback,
            target_r=2.0,
            exit_text="Stop at the 10-day low; target 2x the risk.",
        ),
        Setup(
            "macd_fib",
            "MACD + Fibonacci pullback",
            "1.3",
            "Pullback",
            "In an uptrend, a pullback into the 38-62% retracement, then MACD crosses up on a green candle.",
            _macd_fib,
            target_r=2.0,
            exit_text="Stop at the pullback low; target the swing high or 2x the risk, whichever is further.",
        ),
        Setup(
            "rsi_w",
            "RSI 'W' bounce",
            "4.4",
            "Reversal",
            "RSI dips below 30 twice, the second dip shallower, and crosses back above 30.",
            _rsi_w,
            exit_text="Stop at the pattern's low; exit when RSI reaches 70.",
        ),
        Setup(
            "pin_bar_range",
            "Pin bar at range bottom",
            "7.3",
            "Reversal",
            "After a rally, a tight range; a bullish pin bar (long lower wick) at the range bottom.",
            _pin_bar_range,
            exit_text="Stop at the pin bar's low; target the top of the range.",
        ),
    ]
}

FAMILIES = ["Trend", "Breakout", "Pullback", "Reversal"]


def evaluate(
    setup: Setup, bars: pd.DataFrame, ctx: SetupContext | None = None, symbol: str = ""
) -> pd.DataFrame:
    """The setup's daily frame plus ``entry`` and the final ``target``; signals without a sane
    stop (missing, at or above the entry, or more than 25% below it) are dropped."""
    f = setup.rule(bars, ctx or SetupContext(), symbol)
    f["entry"] = bars["high"]
    risk = f["entry"] - f["stop"]
    sane = f["stop"].notna() & (risk > 0) & (risk <= MAX_RISK_PCT * f["entry"])
    f["signal"] = f["signal"].fillna(False).astype(bool) & sane
    if setup.target_r is not None:
        r_target = f["entry"] + setup.target_r * risk
        f["target"] = np.fmax(f["target"], r_target)
    f.loc[f["target"] <= f["entry"], "target"] = np.nan
    f["exit"] = f["exit"].fillna(False).astype(bool)
    return f
