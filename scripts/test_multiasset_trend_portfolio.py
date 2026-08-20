"""Regression tests for multi-asset funding and cost accounting."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.discover_multiasset_trend_portfolio import (
    close_trade, funding_allows, market_breadth,
)


def position(side: str) -> dict:
    return {
        "symbol": "BTCUSDT", "side": side,
        "entry_time": pd.Timestamp("2025-01-01", tz="UTC"),
        "entry_price": 100.0, "entry_funding": .001,
        "capital_fraction": .25,
    }


def test_positive_funding_is_paid_by_long_and_received_by_short() -> None:
    row = pd.Series({"cumulative_funding": .003})
    timestamp = pd.Timestamp("2025-01-02", tz="UTC")
    long = close_trade(position("LONG"), row, timestamp, 100, "TIMEOUT", .1)
    short = close_trade(position("SHORT"), row, timestamp, 100, "TIMEOUT", .1)
    assert long["funding_return"] == -.002
    assert short["funding_return"] == .002
    assert abs(long["net_equity_return"] - .25 * (-.002 - .001)) < 1e-12
    assert abs(short["net_equity_return"] - .25 * (.002 - .001)) < 1e-12


def test_price_return_is_side_symmetric_before_funding() -> None:
    row = pd.Series({"cumulative_funding": .001})
    timestamp = pd.Timestamp("2025-01-02", tz="UTC")
    long = close_trade(position("LONG"), row, timestamp, 101, "TIMEOUT", 0)
    short = close_trade(position("SHORT"), row, timestamp, 99, "TIMEOUT", 0)
    assert abs(long["net_equity_return"] - .0025) < 1e-12
    assert abs(short["net_equity_return"] - .0025) < 1e-12


def test_market_breadth_uses_only_causal_liquid_members() -> None:
    rows = {
        "A": pd.Series({"trend_direction": 1}),
        "B": pd.Series({"trend_direction": 1}),
        "C": pd.Series({"trend_direction": -1}),
        "D": pd.Series({"trend_direction": -1}),
    }
    assert market_breadth(rows, [("A", 10), ("B", 9), ("C", 8)]) == (2 / 3, 1 / 3)
    assert market_breadth(rows, [("C", 8), ("D", 7)]) == (0, 1)


def test_funding_crowding_veto_is_side_symmetric() -> None:
    assert funding_allows("LONG", .5, 1)
    assert not funding_allows("LONG", 1.5, 1)
    assert funding_allows("SHORT", -.5, 1)
    assert not funding_allows("SHORT", -1.5, 1)


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"PASS {len(tests)} tests")
