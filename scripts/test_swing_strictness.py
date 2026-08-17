"""Regression tests for issue #13 — swing detection in flat price regions.

`find_recent_swing_low`/`find_recent_swing_high` compared a candidate bar with
`<=` / `>=` against its neighbours, so in a flat region every bar satisfied the
condition and the first scanned candidate was returned as a swing. A swing has
to be strictly lower (higher) than every bar in the window, which is also what
`src/engine/support_resistance.py` already does.

The pair of tests per direction matters: the flat case pins the fix, the peak
case pins that the fix did not simply stop the scanner from ever returning.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.indicators.technical import find_recent_swing_high, find_recent_swing_low  # noqa: E402


def test_flat_region_has_no_swing_low():
    df = pd.DataFrame({"low": [10.0] * 30})
    res = find_recent_swing_low(df, window=1, lookback=50)
    assert res is None, f"Flat lows must produce no swing low, got {res}"
    print("test_flat_region_has_no_swing_low passed!")


def test_flat_region_has_no_swing_high():
    df = pd.DataFrame({"high": [10.0] * 30})
    res = find_recent_swing_high(df, window=1, lookback=50)
    assert res is None, f"Flat highs must produce no swing high, got {res}"
    print("test_flat_region_has_no_swing_high passed!")


def test_real_swing_low_still_detected():
    # Dip at index 6 surrounded by a flat plateau: strictly lower than both
    # neighbours, so it stays a swing after the fix.
    lows = [13.0] * 6 + [9.0] + [13.0] * 5
    df = pd.DataFrame({"low": lows})
    res = find_recent_swing_low(df, window=1, lookback=50)
    assert res == 9.0, f"Expected the dip 9.0 to be the swing low, got {res}"
    print("test_real_swing_low_still_detected passed!")


def test_real_swing_high_still_detected():
    highs = [9.0] * 6 + [13.0] + [9.0] * 5
    df = pd.DataFrame({"high": highs})
    res = find_recent_swing_high(df, window=1, lookback=50)
    assert res == 13.0, f"Expected the peak 13.0 to be the swing high, got {res}"
    print("test_real_swing_high_still_detected passed!")


def test_plateau_bottom_is_not_a_swing_low():
    # Two adjacent bars share the lowest value. Neither is strictly lower than
    # the other, so neither may be reported — this is the case the old `<=`
    # got wrong even outside a fully flat series.
    lows = [13.0, 13.0, 13.0, 9.0, 9.0, 13.0, 13.0, 13.0]
    df = pd.DataFrame({"low": lows})
    res = find_recent_swing_low(df, window=1, lookback=50)
    assert res is None, f"A two-bar plateau bottom is not a swing, got {res}"
    print("test_plateau_bottom_is_not_a_swing_low passed!")


if __name__ == "__main__":
    test_flat_region_has_no_swing_low()
    test_flat_region_has_no_swing_high()
    test_real_swing_low_still_detected()
    test_real_swing_high_still_detected()
    test_plateau_bottom_is_not_a_swing_low()
    print("All swing strictness tests passed!")
