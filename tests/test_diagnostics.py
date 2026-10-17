from datetime import date

import numpy as np
import pandas as pd

from trademare.analysis.regime import Regime, RegimeLabel
from trademare.backtest.diagnostics import _quintile, format_study, signal_study
from trademare.core.interfaces import AnalystResult
from trademare.strategy.scanner import ScanResult, SymbolScore


class PredictiveContext:
    """Scores equal each symbol's true future drift, so IC must be strongly positive."""

    def __init__(self, n_symbols=40, n_days=200, noise=False):
        rng = np.random.default_rng(0)
        idx = pd.bdate_range("2024-01-01", periods=n_days)
        self.drifts = {f"S{i}": (i - n_symbols / 2) / 1000 for i in range(n_symbols)}
        self.bars = {}
        for s, d in self.drifts.items():
            close = 100 * np.exp(np.cumsum(d + 0.001 * rng.standard_normal(n_days)))
            self.bars[s] = pd.DataFrame({"close": close}, index=idx)
        self.noise = noise

    def sessions(self, start, end):
        idx = next(iter(self.bars.values())).index
        return idx[(idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end))]

    def evaluate(self, asof, fundamentals=None, keep_scores=True):
        rng = np.random.default_rng(hash(asof) % 2**32)
        scores = []
        for s, d in self.drifts.items():
            value = float(rng.uniform(-1, 1)) if self.noise else float(np.clip(d * 40, -1, 1))
            tech = AnalystResult(analyst="technical", score=value, confidence=1)
            scores.append(
                SymbolScore(
                    symbol=s,
                    name=s,
                    sector="T",
                    close=1,
                    composite=value,
                    confidence=1,
                    liquid=True,
                    analysts={"technical": tech},
                )
            )
        regime = Regime(label=RegimeLabel.NEUTRAL, score=0, index_trend=None, breadth=None, reasons=[])
        return ScanResult(market="US", asof=asof, regime=regime, weights={}, ideas=[], scores=scores)


def test_predictive_signal_has_high_ic():
    study = signal_study(PredictiveContext(), date(2024, 1, 1), date(2024, 12, 31), horizon=20, step=10)
    assert study["composite"]["mean_ic"] > 0.8
    q = study["composite"]["quintile_excess"]
    assert q[5] > q[1]
    assert study["fundamental"]["coverage"] == 0  # no fundamental scores supplied
    assert "signal study" in format_study("US", study)


def test_random_signal_has_no_ic():
    study = signal_study(
        PredictiveContext(noise=True), date(2024, 1, 1), date(2024, 12, 31), horizon=20, step=5
    )
    assert abs(study["composite"]["mean_ic"]) < 0.1


def test_quintiles():
    q = _quintile(pd.Series(range(10)))
    assert q.tolist() == [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]
