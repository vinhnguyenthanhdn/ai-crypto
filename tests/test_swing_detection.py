"""Regression tests for swing detection in flat price regions (issue #13).

Non-strict comparisons (<= / >=) made every bar in a flat region a "swing".
These tests pin the strict-comparison behaviour.
"""
import pandas as pd

from src.indicators.technical import (
    find_recent_swing_high,
    find_recent_swing_low,
)


def test_flat_region_produces_no_swing_low():
    flat = pd.Series([10.0] * 30)
    assert find_recent_swing_low(flat) is None


def test_flat_region_produces_no_swing_high():
    flat = pd.Series([10.0] * 30)
    assert find_recent_swing_high(flat) is None


def test_real_swing_low_still_detected():
    lows = pd.Series([13.0, 13.0, 13.0, 13.0, 13.0, 13.0, 9.0,
                      13.0, 13.0, 13.0, 13.0, 13.0])
    assert find_recent_swing_low(lows) == 9.0


def test_real_swing_high_still_detected():
    highs = pd.Series([9.0, 9.0, 9.0, 9.0, 9.0, 9.0, 13.0,
                       9.0, 9.0, 9.0, 9.0, 9.0])
    assert find_recent_swing_high(highs) == 13.0
