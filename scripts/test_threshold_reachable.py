"""Guard: ngưỡng BUY phải đạt được về mặt số học khi 4/6 layer bị ghim NEUTRAL.

Xem issue #5. Test không cần dữ liệu thật — dựng chuỗi OHLC tổng hợp đủ dài để
qua warmup, và chấm hai trường hợp: ngưỡng mặc định (không đạt được) và một
ngưỡng nằm trong tầm (đạt được).
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src import config  # noqa: E402
from src.backtest.engine import max_attainable_score, run_backtest  # noqa: E402
from src.backtest.short_engine import run_backtest_short  # noqa: E402


def _synthetic_df(n=400, seed=0):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC"),
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": rng.uniform(100, 200, n),
    })


def main():
    failures = []

    def check(name, cond, detail=""):
        print(f"  {'PASS' if cond else 'FAIL'}  {name}")
        if not cond:
            failures.append(f"{name}: {detail}")

    print("=== issue #5: BUY threshold reachability guard ===")

    max_score = max_attainable_score()
    check(
        "max_attainable_score tính từ config.WEIGHTS, không hard-code",
        abs(max_score - 69.0) < 1e-9,
        f"max_attainable_score()={max_score}, kỳ vọng 69.0 với WEIGHTS hiện tại",
    )
    check(
        "ngưỡng mặc định nằm trên trần",
        config.BUY_SCORE_THRESHOLD > max_score,
        f"BUY_SCORE_THRESHOLD={config.BUY_SCORE_THRESHOLD} <= {max_score}",
    )

    df = _synthetic_df()

    res = run_backtest(df, symbol="BTC/USDT", timeframe="5m")
    check(
        "run_backtest báo entry_possible=False với ngưỡng mặc định",
        res["entry_possible"] is False,
        f"entry_possible={res['entry_possible']}",
    )
    check(
        "short_circuit_reason nêu cả trần lẫn ngưỡng",
        res["short_circuit_reason"] == f"MAX_SCORE_{max_score:.2f}_BELOW_THRESHOLD_"
                                       f"{config.BUY_SCORE_THRESHOLD:.2f}",
        f"short_circuit_reason={res['short_circuit_reason']}",
    )

    res_ok = run_backtest(df, symbol="BTC/USDT", timeframe="5m", buy_threshold=60.0)
    check(
        "guard KHÔNG bắn với ngưỡng đạt được (60)",
        res_ok["entry_possible"] is True and res_ok["short_circuit_reason"] is None,
        f"entry_possible={res_ok['entry_possible']}, reason={res_ok['short_circuit_reason']}",
    )

    res_short = run_backtest_short(df, symbol="BTC/USDT", timeframe="5m")
    check(
        "run_backtest_short cũng có guard với ngưỡng mặc định",
        res_short["entry_possible"] is False,
        f"entry_possible={res_short['entry_possible']}",
    )
    res_short_ok = run_backtest_short(df, symbol="BTC/USDT", timeframe="5m", short_threshold=60.0)
    check(
        "run_backtest_short KHÔNG bắn với ngưỡng đạt được (60)",
        res_short_ok["entry_possible"] is True,
        f"entry_possible={res_short_ok['entry_possible']}",
    )

    total = 7
    print(f"\n=== {total - len(failures)}/{total} PASS ===")
    if failures:
        print("\nFAILED:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
