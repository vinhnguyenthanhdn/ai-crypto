"""E2E: dữ liệu BTC thật -> Paper engine -> SQLite -> trade parity."""
import gzip
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402
from src.backtest import paper_engine  # noqa: E402
from src.engine import risk  # noqa: E402
from src.engine import staggered_pullback as strategy  # noqa: E402


FLOW_CACHE = Path("data/backtests/binance_btcusdt_spot_5m_flow_9y.json.gz")
START = pd.Timestamp("2025-01-01")
END = pd.Timestamp("2025-07-01")


def _load_featured():
    with gzip.open(FLOW_CACHE, "rt", encoding="utf-8") as source:
        raw = json.load(source)
    frame = pd.DataFrame(raw["rows"])
    frame["ts"] = pd.to_datetime(frame.ts, unit="ms")
    return strategy.add_features(strategy.aggregate_closed_4h(frame))


def main():
    if not np.isclose(
        risk.round_trip_cost_pct(), strategy.FROZEN_CONTRACT.round_trip_cost_pct,
        rtol=0, atol=1e-12,
    ):
        raise AssertionError("runtime fee/slippage không khớp frozen round-trip cost")
    featured = _load_featured()
    reference = strategy.replay(featured, START, END)
    if not reference:
        raise AssertionError("E2E window phải có trade để test lifecycle")

    original_db = config.DB_PATH
    try:
        with tempfile.TemporaryDirectory(prefix="staggered-paper-e2e-") as temp_dir:
            config.DB_PATH = Path(temp_dir) / "paper-e2e.db"
            result = paper_engine.run_staggered_accelerated_replay(
                featured, START, END, contract=strategy.FROZEN_CONTRACT,
            )
            assert len(result["trades"]) == len(reference)
            mismatches = []
            for index, (expected, actual) in enumerate(zip(reference, result["trades"])):
                exact = ("side", "excursion_id", "exit_reason")
                numeric = ("entry_price", "exit_price", "stop")
                for key in exact:
                    if expected[key] != actual[key]:
                        mismatches.append((index, key, expected[key], actual[key]))
                for key in numeric:
                    if not np.isclose(expected[key], actual[key], rtol=0, atol=1e-7):
                        mismatches.append((index, key, expected[key], actual[key]))
                for key in ("entry_ts", "exit_ts"):
                    if pd.Timestamp(expected[key]) != pd.Timestamp(actual[key]):
                        mismatches.append((index, key, str(expected[key]), str(actual[key])))
            assert not mismatches, mismatches[:10]

            with sqlite3.connect(config.DB_PATH) as conn:
                counts = dict(conn.execute(
                    "SELECT type, COUNT(*) FROM event_log GROUP BY type"
                ).fetchall())
                ledger_count = conn.execute("SELECT COUNT(*) FROM equity_ledger").fetchone()[0]
                orphan_count = conn.execute(
                    "SELECT COUNT(*) FROM equity_ledger l "
                    "LEFT JOIN event_log e ON e.trade_id=l.trade_id AND e.type='EXIT' "
                    "WHERE e.id IS NULL"
                ).fetchone()[0]
                event_bounds = conn.execute("SELECT MIN(ts), MAX(ts) FROM event_log").fetchone()
            assert counts.get("ENTRY") == counts.get("EXIT") == len(reference)
            assert ledger_count == len(reference)
            assert orphan_count == 0
            assert event_bounds[0].startswith("2025-") and event_bounds[1].startswith("2025-")
            assert result["feature_snapshot_count"] == len(featured[(featured.index >= START) & (featured.index < END)])
            assert result["open_position_count"] == 0
    finally:
        config.DB_PATH = original_db
    print(f"=== staggered Paper E2E real-data: {len(reference)}/{len(reference)} trade parity PASS ===")


if __name__ == "__main__":
    main()
