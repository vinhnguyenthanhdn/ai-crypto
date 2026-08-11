import pandas as pd
import numpy as np
from src.indicators.technical import find_recent_swing_low, find_recent_swing_high

def test_swing_low_no_lookahead():
    # Construct a dataframe where low dip occurs at index 7.
    # With window=3, index 7 requires bars 8, 9, 10 to confirm.
    lows = [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 5.0, 10.0, 10.0, 10.0, 10.0]
    df = pd.DataFrame({"low": lows})
    
    # When decision_idx = 8 (bar 8), right window (8, 9, 10) is incomplete because bar 10 > decision_idx 8.
    res_at_8 = find_recent_swing_low(df, idx=8, window=3, lookback=10)
    assert res_at_8 is None, f"Expected None at decision_idx=8, got {res_at_8}"
    
    # When decision_idx = 10 (bar 10), right window (8, 9, 10) is fully available <= 10.
    res_at_10 = find_recent_swing_low(df, idx=10, window=3, lookback=10)
    assert res_at_10 == 5.0, f"Expected 5.0 at decision_idx=10, got {res_at_10}"
    print("test_swing_low_no_lookahead passed!")

if __name__ == "__main__":
    test_swing_low_no_lookahead()
