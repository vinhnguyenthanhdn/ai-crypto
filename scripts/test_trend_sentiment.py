"""Deterministic regression tests for trend/sentiment adaptive risk."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.engine import trend_sentiment as strategy  # noqa: E402


def test_adaptive_stop_and_fixed_account_risk():
    contract = strategy.Contract(base_stop_atr=1.5, maximum_stop_atr=3.0,
                                 trend_strength_for_max_stop=2.0,
                                 risk_per_episode_pct=0.5, maximum_capital_fraction=1.0)
    weak = strategy.position_plan(100, 1, "LONG", 0, contract)
    strong = strategy.position_plan(100, 1, "LONG", 2, contract)
    assert weak["stop_price"] == 98.5
    assert strong["stop_price"] == 97.0
    assert strong["take_profit_price"] == 104.5
    assert strong["capital_fraction"] < weak["capital_fraction"]
    assert abs(strong["capital_fraction"] * 3 - .5) < 1e-12


def test_short_is_symmetric():
    contract = strategy.Contract(base_stop_atr=2, maximum_stop_atr=2, risk_reward=2)
    plan = strategy.position_plan(100, 1, "SHORT", 1, contract)
    assert plan["stop_price"] == 102
    assert plan["take_profit_price"] == 96


def test_context_is_delayed_until_bar_close():
    index = pd.date_range("2025-01-01", periods=12 * 24 * 60 // 5, freq="5min", tz="UTC")
    close = pd.Series(range(len(index)), index=index, dtype=float) + 100
    source = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1,
                           "close": close, "volume": 1}, index=index)
    contract = strategy.Contract(context_fast_bars=2, context_slow_bars=3,
                                 daily_sma_days=2, entry_ema_bars=2)
    result = strategy.add_features(source, None, contract)
    first_daily_available = pd.Timestamp("2025-01-03", tz="UTC")
    assert pd.isna(result.loc[first_daily_available - pd.Timedelta(hours=1), "day_sma"])
    assert pd.notna(result.loc[first_daily_available, "day_sma"])


def test_session_volume_threshold_uses_prior_bars():
    index = pd.date_range("2025-01-01", periods=70 * 24 * 2, freq="30min", tz="UTC")
    close = pd.Series(range(len(index)), index=index, dtype=float) / 100 + 100
    source = pd.DataFrame({"open": close - .1, "high": close + .2, "low": close - .2,
                           "close": close, "volume": 1}, index=index)
    contract = strategy.Contract(entry_mode="session_momentum", entry_timeframe="30min",
                                 context_fast_bars=2, context_slow_bars=3,
                                 daily_sma_days=2, entry_ema_bars=2,
                                 volume_lookback_bars=10, volume_quantile=.8)
    result = strategy.add_features(source, None, contract)
    spike = next(ts for ts in result.index[200:] if ts.hour % 4 == 0 and ts.minute == 0)
    source.loc[spike, "volume"] = 100
    changed = strategy.add_features(source, None, contract)
    assert bool(changed.loc[spike, "long_signal"])


def test_breakout_threshold_excludes_current_bar():
    index = pd.date_range("2025-01-01", periods=180 * 24 * 12, freq="5min", tz="UTC")
    close = pd.Series(range(len(index)), index=index, dtype=float) / 100 + 100
    source = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1,
                           "close": close, "volume": 1}, index=index)
    contract = strategy.Contract(
        entry_mode="breakout_continuation", entry_timeframe="1h",
        context_fast_bars=2, context_slow_bars=3, daily_sma_days=2,
        breakout_lookback_bars=4,
    )
    featured = strategy.add_features(source, None, contract)
    row = featured.iloc[-1]
    previous = featured.iloc[-5:-1]
    assert row.prior_breakout_high == previous.high.max()
    assert row.prior_breakout_low == previous.low.min()


def main():
    tests = [value for name, value in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)}/{len(tests)} passed")


if __name__ == "__main__":
    main()
