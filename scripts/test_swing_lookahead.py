import pandas as pd
from src.indicators.technical import find_recent_swing_low, find_recent_swing_high


def test_swing_low_no_lookahead():
    # Non-flat rising baseline with a dip at index 4 (value 5.0) and window=1.
    # Bar 4 requires right window [bar 5] (lows[5] = 10.0) to confirm.
    # At decision_idx=4 (forming bar 4) or decision_idx=5 (bar 5 is forming/current decision point),
    # bar 4 cannot be confirmed yet because bar 5 is forming or unavailable (i + window >= end).
    lows = [10.0, 11.0, 12.0, 13.0, 5.0, 14.0, 15.0]
    df = pd.DataFrame({"low": lows})

    # At decision_idx = 5, candidate i = 4 gives i + window = 5 >= end (5), so bar 4 is not confirmed.
    res_at_5 = find_recent_swing_low(df, idx=5, window=1, lookback=10)
    assert res_at_5 is None, f"Expected None at decision_idx=5, got {res_at_5}"

    # At decision_idx = 6, end = 6, candidate i = 4 gives i + window = 5 < end (6), so bar 4 is confirmed.
    res_at_6 = find_recent_swing_low(df, idx=6, window=1, lookback=10)
    assert res_at_6 == 5.0, f"Expected 5.0 at decision_idx=6, got {res_at_6}"
    print("test_swing_low_no_lookahead passed!")


def test_swing_high_no_lookahead():
    # Non-flat falling baseline with a peak at index 4 (value 25.0) and window=1.
    # Bar 4 requires right window [bar 5] (highs[5] = 10.0) to confirm.
    highs = [20.0, 19.0, 18.0, 17.0, 25.0, 10.0, 9.0]
    df = pd.DataFrame({"high": highs})

    # At decision_idx = 5, candidate i = 4 gives i + window = 5 >= end (5), so bar 4 is not confirmed.
    res_at_5 = find_recent_swing_high(df, idx=5, window=1, lookback=10)
    assert res_at_5 is None, f"Expected None at decision_idx=5, got {res_at_5}"

    # At decision_idx = 6, end = 6, candidate i = 4 gives i + window = 5 < end (6), so bar 4 is confirmed.
    res_at_6 = find_recent_swing_high(df, idx=6, window=1, lookback=10)
    assert res_at_6 == 25.0, f"Expected 25.0 at decision_idx=6, got {res_at_6}"
    print("test_swing_high_no_lookahead passed!")


if __name__ == "__main__":
    test_swing_low_no_lookahead()
    test_swing_high_no_lookahead()
