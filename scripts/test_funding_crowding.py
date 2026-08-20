"""Deterministic tests for the frozen funding-crowding production core."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.engine import funding_crowding as strategy


def row(direction=1, strength=2, funding_z=0, volume=100, signal=True):
    return pd.Series({
        "trailing_dollar_volume": volume, "long_signal": signal and direction > 0,
        "short_signal": signal and direction < 0, "funding_z": funding_z,
        "entry_atr": 1, "trend_strength": strength, "trend_direction": direction,
        "open": 100, "high": 101, "low": 99,
    })


def test_rank_uses_liquidity_crowding_and_strength():
    rows = {"A": row(strength=1, volume=300), "B": row(strength=3, volume=200),
            "C": row(strength=2, funding_z=1, volume=100)}
    decisions = strategy.rank_entries(rows, set(), 2)
    assert [item["symbol"] for item in decisions] == ["B", "A"]
    assert strategy.rank_entries(rows, {"B"}, 1)[0]["symbol"] == "A"


def test_short_crowding_is_symmetric():
    assert strategy.funding_allows("LONG", -.1)
    assert not strategy.funding_allows("LONG", .1)
    assert strategy.funding_allows("SHORT", .1)
    assert not strategy.funding_allows("SHORT", -.1)


def test_entry_plan_widens_stop_without_increasing_account_risk():
    weak = strategy.entry_plan(100, {"side": "LONG", "atr": 1, "trend_strength": 0})
    strong = strategy.entry_plan(100, {"side": "LONG", "atr": 1, "trend_strength": 2})
    assert weak["stop_price"] == 98
    assert strong["stop_price"] == 96
    assert strong["take_profit_price"] == 116
    assert strong["capital_fraction"] < weak["capital_fraction"]


def test_exit_is_adverse_first_and_timeout_is_exact():
    position = {"side": "LONG", "stop_price": 99, "take_profit_price": 101,
                "entry_time": pd.Timestamp("2025-01-01", tz="UTC")}
    both = row(); both["low"], both["high"] = 98, 102
    assert strategy.exit_decision(position, both, pd.Timestamp("2025-01-01 01:00", tz="UTC")) == (99, "STOP_LOSS")
    flat = row(); flat["low"], flat["high"] = 99.5, 100.5
    assert strategy.exit_decision(position, flat, pd.Timestamp("2025-01-02", tz="UTC")) == (100, "TIMEOUT")


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"PASS {len(tests)} tests")
