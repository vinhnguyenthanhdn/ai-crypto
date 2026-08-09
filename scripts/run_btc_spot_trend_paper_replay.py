"""Run full accelerated Paper/SQLite parity for frozen BTC Spot trend."""
import argparse
import gzip
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from discover_btc_spot_trend_9y import replay  # noqa: E402
from src import config  # noqa: E402
from src.backtest.btc_spot_trend_paper_engine import run_accelerated_replay  # noqa: E402
from src.engine import btc_spot_trend as strategy  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    source, db_path = Path(args.data), Path(args.db)
    if db_path.exists() and not args.overwrite:
        raise FileExistsError(f"DB đã tồn tại: {db_path}")
    if db_path.exists():
        db_path.unlink()
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(db_path) + suffix)
        if sidecar.exists():
            sidecar.unlink()
    with gzip.open(source, "rt", encoding="utf-8") as handle:
        raw = pd.DataFrame(json.load(handle)["rows"])
    raw["ts"] = pd.to_datetime(raw.ts, unit="ms")
    daily = strategy.aggregate_closed_daily(raw)
    featured = strategy.add_features(daily)
    start, end = daily.index[0], daily.index[-1] + pd.Timedelta(days=1)
    reference, _, _ = replay(daily, featured.target_exposure, start, end, 0.12)
    production_db = Path(config.DB_PATH).resolve()
    if db_path.resolve() == production_db:
        raise RuntimeError("từ chối ghi đè DB runtime")
    config.DB_PATH = db_path
    paper = run_accelerated_replay(featured, start, end, cost_pct=0.12)
    mismatch = None
    if len(reference) != len(paper["daily_returns"]) or not np.allclose(
            reference, paper["daily_returns"], atol=1e-12, rtol=0):
        mismatch = {"reference_rows": len(reference), "paper_rows": len(paper["daily_returns"]),
                    "max_abs": float(np.max(np.abs(reference - paper["daily_returns"])))
                    if len(reference) == len(paper["daily_returns"]) else None}
    with sqlite3.connect(db_path) as conn:
        counts = {kind: conn.execute("SELECT COUNT(*) FROM event_log WHERE type=?", (kind,)).fetchone()[0]
                  for kind in ("ENTRY", "REBALANCE", "EXIT", "REPLAY_STARTED", "REPLAY_COMPLETED")}
        counts["features"] = conn.execute("SELECT COUNT(*) FROM feature_snapshot").fetchone()[0]
        counts["signals"] = conn.execute("SELECT COUNT(*) FROM signal_log").fetchone()[0]
        counts["ledger"] = conn.execute("SELECT COUNT(*) FROM equity_ledger").fetchone()[0]
        counts["open_positions"] = conn.execute("SELECT COUNT(*) FROM position_state").fetchone()[0]
        orphan = conn.execute(
            "SELECT COUNT(*) FROM equity_ledger l LEFT JOIN event_log e "
            "ON e.trade_id=l.trade_id AND e.type='EXIT' WHERE e.id IS NULL"
        ).fetchone()[0]
    lifecycle_pass = (counts["ENTRY"] == counts["EXIT"] == counts["ledger"]
                      and counts["ENTRY"] > 0 and counts["open_positions"] == 0
                      and counts["features"] == counts["signals"] == len(daily)
                      and orphan == 0)
    expected_equity = float(config.ACCOUNT_EQUITY_USD * np.prod(1 + reference))
    result = {"passed": bool(mismatch is None and lifecycle_pass and
              np.isclose(expected_equity, paper["final_equity_usd"], atol=1e-8, rtol=0)),
              "mode": "accelerated_paper_sqlite_lifecycle", "clock": "simulated",
              "dataset": {"source": str(source.resolve()), "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                          "start": str(start), "end": str(end), "days": len(daily)},
              "contract": strategy.FROZEN_CONTRACT.manifest(),
              "strategy_package_id": strategy.PACKAGE_ID,
              "parity": {"passed": mismatch is None, "mismatch": mismatch,
                         "reference_final_equity_usd": expected_equity,
                         "paper_final_equity_usd": paper["final_equity_usd"]},
              "lifecycle": {"passed": lifecycle_pass, "counts": counts, "orphan_ledger": orphan},
              "performance": {key: paper[key] for key in ("initial_equity_usd", "final_equity_usd",
                                                          "net_return_pct", "max_drawdown_pct")},
              "database": {"path": str(db_path.resolve()), "sha256": hashlib.sha256(db_path.read_bytes()).hexdigest()}}
    Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
