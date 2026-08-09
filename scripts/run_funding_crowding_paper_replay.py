"""Run full accelerated Paper/SQLite lifecycle for funding-crowding sleeve."""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

import numpy as np

from src import config
from src.backtest.funding_crowding_paper_engine import run_accelerated_replay
from src.engine import funding_crowding as strategy


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", default="data/backtests/multiasset_funding_crowding_5y.json")
    parser.add_argument("--db", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    db_path = Path(args.db)
    if db_path.exists() and not args.overwrite:
        raise FileExistsError(f"database exists: {db_path}")
    if db_path.exists():
        db_path.unlink()
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(db_path) + suffix)
        if sidecar.exists():
            sidecar.unlink()
    production_db = Path(config.DB_PATH).resolve()
    if db_path.resolve() == production_db:
        raise RuntimeError("refusing to overwrite runtime database")
    reference_path = Path(args.reference)
    reference = json.loads(reference_path.read_text())
    trades = [trade for split in ("train", "validation", "test")
              for trade in reference["trades"][split]]
    trades.sort(key=lambda item: (item["exit_time"], item["symbol"]))
    expected_returns = np.asarray([trade["net_equity_return"] for trade in trades])
    config.DB_PATH = db_path
    paper = run_accelerated_replay(trades)
    parity = len(expected_returns) == len(paper["returns"]) and np.allclose(
        expected_returns, paper["returns"], atol=1e-12, rtol=0
    )
    with sqlite3.connect(db_path) as conn:
        counts = {kind: conn.execute(
            "SELECT COUNT(*) FROM event_log WHERE type=?", (kind,)
        ).fetchone()[0] for kind in ("ENTRY", "EXIT", "REPLAY_STARTED", "REPLAY_COMPLETED")}
        counts["features"] = conn.execute("SELECT COUNT(*) FROM feature_snapshot").fetchone()[0]
        counts["signals"] = conn.execute("SELECT COUNT(*) FROM signal_log").fetchone()[0]
        counts["ledger"] = conn.execute("SELECT COUNT(*) FROM equity_ledger").fetchone()[0]
        counts["open_positions"] = conn.execute("SELECT COUNT(*) FROM position_state").fetchone()[0]
        orphan = conn.execute(
            "SELECT COUNT(*) FROM equity_ledger l LEFT JOIN event_log e "
            "ON e.trade_id=l.trade_id AND e.type='EXIT' WHERE e.id IS NULL"
        ).fetchone()[0]
        halted_days = conn.execute(
            "SELECT COUNT(*) FROM daily_pnl WHERE trading_halted=1"
        ).fetchone()[0]
    lifecycle = (
        counts["ENTRY"] == counts["EXIT"] == counts["ledger"] == len(trades)
        and counts["features"] == counts["signals"] == len(trades)
        and counts["open_positions"] == 0 and orphan == 0
        and paper["counters"]["max_concurrent"] <= strategy.FROZEN_CONTRACT.max_concurrent
    )
    expected_equity = float(config.ACCOUNT_EQUITY_USD * np.prod(1 + expected_returns))
    passed = bool(parity and lifecycle and np.isclose(
        expected_equity, paper["final_equity_usd"], atol=1e-8, rtol=0
    ))
    output = {
        "passed": passed, "mode": "accelerated_paper_sqlite_lifecycle",
        "clock": "simulated", "strategy_package_id": strategy.PACKAGE_ID,
        "contract": strategy.FROZEN_CONTRACT.manifest(),
        "dataset": {"reference": str(reference_path.resolve()),
                    "sha256": hashlib.sha256(reference_path.read_bytes()).hexdigest(),
                    "trades": len(trades)},
        "parity": {"passed": parity, "expected_final_equity_usd": expected_equity,
                   "paper_final_equity_usd": paper["final_equity_usd"]},
        "lifecycle": {"passed": lifecycle, "counts": counts,
                      "orphan_ledger": orphan, "halted_days": halted_days},
        "performance": {key: paper[key] for key in (
            "initial_equity_usd", "final_equity_usd", "net_return_pct", "max_drawdown_pct"
        )},
    }
    output_path = Path(args.out)
    output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    output["database"] = {"path": str(db_path.resolve()),
                          "sha256": hashlib.sha256(db_path.read_bytes()).hexdigest()}
    output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output, indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
