"""Deterministic lifecycle regression cho accelerated staggered Paper engine."""
import sqlite3
import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402
from src.backtest import paper_engine  # noqa: E402
from src.engine import staggered_pullback as strategy  # noqa: E402


def main():
    original_db = config.DB_PATH
    with tempfile.TemporaryDirectory(prefix="staggered-paper-test-") as temp_dir:
        config.DB_PATH = Path(temp_dir) / "replay.db"
        index = pd.date_range("2025-01-01", periods=4, freq="4h")
        featured = pd.DataFrame({
            "open": [100.0, 100.0, 99.0, 102.0],
            "high": [101.0, 101.0, 100.0, 103.0],
            "low": [99.0, 98.0, 98.0, 101.0],
            "close": [100.0, 99.0, 100.0, 102.0],
            "z": [-1.0, -1.0, 0.6, 0.7],
            "atr": [1.0, 1.0, 1.0, 1.0],
            "trend_ema": [99.0, 98.0, 99.0, 101.0],
        }, index=index)
        contract = strategy.Contract(
            z_lookback_bars=2, trend_ema_bars=2, entry_z=0.5,
            exit_z=0.5, stop_atr=10.0, max_tranches=2,
        )
        result = paper_engine.run_staggered_accelerated_replay(
            featured, index[0], index[-1] + pd.Timedelta(hours=4),
            contract=contract,
        )
        reference = strategy.replay(
            featured, index[0], index[-1] + pd.Timedelta(hours=4), contract,
        )

        assert len(reference) == len(result["trades"]) == 2
        assert result["counters"]["entries"] == 2
        assert result["counters"]["mean_exits"] == 2
        assert result["equity_ledger_count"] == 2
        assert result["open_position_count"] == 0
        assert result["machine_state"]["completed"] is True
        for expected, actual in zip(reference, result["trades"]):
            for key in ("side", "excursion_id", "entry_price", "exit_price", "exit_reason"):
                assert expected[key] == actual[key], (key, expected[key], actual[key])

        with sqlite3.connect(config.DB_PATH) as conn:
            entry_count = conn.execute("SELECT COUNT(*) FROM event_log WHERE type='ENTRY'").fetchone()[0]
            exit_count = conn.execute("SELECT COUNT(*) FROM event_log WHERE type='EXIT'").fetchone()[0]
            event_years = conn.execute(
                "SELECT MIN(substr(ts,1,4)), MAX(substr(ts,1,4)) FROM event_log"
            ).fetchone()
            daily_days = conn.execute("SELECT COUNT(*) FROM daily_pnl").fetchone()[0]
        assert entry_count == exit_count == 2
        assert event_years == ("2025", "2025")
        assert daily_days == 1
    config.DB_PATH = original_db
    print("=== staggered accelerated Paper lifecycle: 1/1 PASS ===")


if __name__ == "__main__":
    try:
        main()
    finally:
        # Khôi phục ngay cả khi assertion fail để test khác trong cùng process an toàn.
        if "original_db" in globals():
            config.DB_PATH = original_db
