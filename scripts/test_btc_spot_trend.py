"""Regression tests for the frozen BTC Spot trend production core."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.engine import btc_spot_trend as strategy  # noqa: E402


def frame(values):
    index = pd.date_range("2025-01-01", periods=len(values), freq="1D")
    close = np.asarray(values, dtype=float)
    return pd.DataFrame({"open": close, "high": close, "low": close, "close": close}, index=index)


def test_trend_and_exposure_cap():
    featured = strategy.add_features(frame(np.linspace(100, 220, 80)))
    decision = strategy.decision_at(featured)
    assert decision["action"] == "LONG"
    assert 0 < decision["target_exposure"] <= 1


def test_cash_below_buffered_sma():
    values = np.r_[np.linspace(100, 200, 79), 100]
    decision = strategy.decision_at(strategy.add_features(frame(values)))
    assert decision["action"] == "CASH"
    assert decision["target_exposure"] == 0


def test_future_data_cannot_change_prior_decision():
    original = frame(np.linspace(100, 220, 90))
    prior = strategy.decision_at(strategy.add_features(original.iloc[:80]))
    changed = original.copy()
    changed.iloc[80:, changed.columns.get_loc("close")] *= 10
    observed = strategy.decision_at(strategy.add_features(changed.iloc[:80]))
    assert prior == observed


def main():
    tests = [test_trend_and_exposure_cap, test_cash_below_buffered_sma,
             test_future_data_cannot_change_prior_decision]
    for test in tests:
        test()
    print(f"PASS {len(tests)}/{len(tests)}")


if __name__ == "__main__":
    main()
