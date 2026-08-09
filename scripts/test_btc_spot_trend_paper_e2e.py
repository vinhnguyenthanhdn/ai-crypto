"""Short synthetic E2E for BTC Spot trend SQLite lifecycle."""
import sqlite3
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402
from src.backtest.btc_spot_trend_paper_engine import run_accelerated_replay  # noqa: E402
from src.engine import btc_spot_trend as strategy  # noqa: E402


def main():
    index = pd.date_range("2025-01-01", periods=140, freq="1D")
    close = np.r_[np.linspace(100, 200, 100), np.linspace(200, 80, 40)]
    daily = pd.DataFrame({"open": close, "high": close, "low": close, "close": close}, index=index)
    featured = strategy.add_features(daily)
    with tempfile.TemporaryDirectory() as directory:
        config.DB_PATH = Path(directory) / "paper.db"
        result = run_accelerated_replay(featured, index[0], index[-1] + pd.Timedelta(days=1))
        with sqlite3.connect(config.DB_PATH) as conn:
            entries = conn.execute("SELECT COUNT(*) FROM event_log WHERE type='ENTRY'").fetchone()[0]
            exits = conn.execute("SELECT COUNT(*) FROM event_log WHERE type='EXIT'").fetchone()[0]
            ledger = conn.execute("SELECT COUNT(*) FROM equity_ledger").fetchone()[0]
            positions = conn.execute("SELECT COUNT(*) FROM position_state").fetchone()[0]
        assert entries == exits == ledger and entries > 0
        assert positions == 0
        assert result["counters"]["days"] == len(daily)
    print("PASS synthetic SQLite lifecycle")


if __name__ == "__main__":
    main()
