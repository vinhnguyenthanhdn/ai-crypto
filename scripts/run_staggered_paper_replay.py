"""Chạy accelerated Paper lifecycle trên lịch sử và audit production parity."""
import argparse
import gzip
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402
from src.backtest import paper_engine  # noqa: E402
from src.engine import risk  # noqa: E402
from src.engine import staggered_pullback as strategy  # noqa: E402


def _load_source(path: Path) -> pd.DataFrame:
    with gzip.open(path, "rt", encoding="utf-8") as source:
        raw = json.load(source)
    frame = pd.DataFrame(raw["rows"])
    frame["ts"] = pd.to_datetime(frame.ts, unit="ms")
    return frame


def _compare(reference: list[dict], actual: list[dict]) -> list:
    mismatches = []
    if len(reference) != len(actual):
        mismatches.append({"trade_count": [len(reference), len(actual)]})
    for index, (expected, observed) in enumerate(zip(reference, actual)):
        differences = []
        for key in ("side", "excursion_id", "exit_reason"):
            if expected[key] != observed[key]:
                differences.append(f"{key}: {expected[key]!r} != {observed[key]!r}")
        for key in ("entry_price", "exit_price", "stop"):
            if not np.isclose(expected[key], observed[key], rtol=0, atol=1e-7):
                differences.append(f"{key}: {expected[key]!r} != {observed[key]!r}")
        for key in ("entry_ts", "exit_ts"):
            if pd.Timestamp(expected[key]) != pd.Timestamp(observed[key]):
                differences.append(f"{key}: {expected[key]!r} != {observed[key]!r}")
        if differences:
            mismatches.append({"trade_index": index, "differences": differences})
            if len(mismatches) >= 20:
                break
    return mismatches


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--flow-cache", required=True)
    parser.add_argument("--split-artifact", required=True)
    parser.add_argument("--years", type=int, default=3)
    parser.add_argument("--db", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    source = Path(args.flow_cache).resolve()
    split = json.loads(Path(args.split_artifact).read_text(encoding="utf-8"))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != split["dataset"]["sha256"]:
        raise AssertionError("dataset hash không khớp split artifact")
    if args.years <= 0:
        raise ValueError("--years phải dương")
    if not np.isclose(
        risk.round_trip_cost_pct(), strategy.FROZEN_CONTRACT.round_trip_cost_pct,
        rtol=0, atol=1e-12,
    ):
        raise AssertionError("runtime fee/slippage không khớp frozen cost contract")

    replay_db = Path(args.db).resolve()
    production_db = Path(config.DB_PATH).resolve()
    if replay_db == production_db:
        raise RuntimeError("từ chối dùng DB runtime thật cho historical replay")
    if replay_db.exists() and not args.overwrite:
        raise FileExistsError(f"DB đã tồn tại: {replay_db}; dùng --overwrite để tạo lại")
    if replay_db.exists():
        replay_db.unlink()
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(replay_db) + suffix)
        if sidecar.exists():
            sidecar.unlink()

    end = pd.Timestamp(split["dataset"]["end"])
    start = end - pd.DateOffset(years=args.years)
    source_5m = _load_source(source)
    bars = strategy.aggregate_closed_4h(source_5m)
    featured = strategy.add_features(bars)
    reference = strategy.replay(featured, start, end)

    config.DB_PATH = replay_db
    replay = paper_engine.run_staggered_accelerated_replay(
        featured, start, end, contract=strategy.FROZEN_CONTRACT,
        risk_per_excursion_pct=config.STAGGERED_PULLBACK_RISK_PER_EXCURSION_PCT,
    )
    mismatches = _compare(reference, replay["trades"])

    with sqlite3.connect(replay_db) as conn:
        db_counts = {
            "positions": conn.execute("SELECT COUNT(*) FROM position_state").fetchone()[0],
            "entries": conn.execute("SELECT COUNT(*) FROM event_log WHERE type='ENTRY'").fetchone()[0],
            "exits": conn.execute("SELECT COUNT(*) FROM event_log WHERE type='EXIT'").fetchone()[0],
            "ledger": conn.execute("SELECT COUNT(*) FROM equity_ledger").fetchone()[0],
            "signals": conn.execute("SELECT COUNT(*) FROM signal_log").fetchone()[0],
            "features": conn.execute("SELECT COUNT(*) FROM feature_snapshot").fetchone()[0],
        }
        orphan_ledger = conn.execute(
            "SELECT COUNT(*) FROM equity_ledger l "
            "LEFT JOIN event_log e ON e.trade_id=l.trade_id AND e.type='EXIT' "
            "WHERE e.id IS NULL"
        ).fetchone()[0]
        event_bounds = conn.execute("SELECT MIN(ts), MAX(ts) FROM event_log").fetchone()

    lifecycle_pass = (
        db_counts["positions"] == 0
        and db_counts["entries"] == db_counts["exits"] == db_counts["ledger"] == len(reference)
        and orphan_ledger == 0
        and replay["machine_state"]["completed"] is True
    )
    pnl_values = np.asarray([
        trade["accounting"]["net_pnl_usd"] for trade in replay["trades"]
    ], dtype=float)
    gains = float(pnl_values[pnl_values > 0].sum())
    losses = float(abs(pnl_values[pnl_values < 0].sum()))
    excursion_count = len({trade["excursion_id"] for trade in replay["trades"]})
    side_breakdown = {}
    for side in ("LONG", "SHORT"):
        side_trades = [trade for trade in replay["trades"] if trade["side"] == side]
        side_breakdown[side] = {
            "tickets": len(side_trades),
            "net_pnl_usd": round(sum(
                trade["accounting"]["net_pnl_usd"] for trade in side_trades
            ), 6),
        }
    result = {
        "passed": not mismatches and lifecycle_pass,
        "mode": "accelerated_paper_sqlite_lifecycle",
        "clock": "simulated",
        "dataset": {
            "source": str(source), "sha256": digest,
            "start": str(start), "end": str(end), "years": args.years,
        },
        "database": {
            "path": str(replay_db), "counts": db_counts,
            "orphan_ledger": orphan_ledger,
            "event_bounds": list(event_bounds),
        },
        "parity": {
            "reference_trades": len(reference),
            "paper_lifecycle_trades": len(replay["trades"]),
            "trade_for_trade_pass": not mismatches,
            "mismatches": mismatches,
        },
        "performance": {
            "initial_equity_usd": replay["initial_equity_usd"],
            "final_equity_usd": replay["final_equity_usd"],
            "net_return_pct": replay["net_return_pct"],
            "max_drawdown_pct": replay["max_drawdown_pct"],
            "profit_factor": round(gains / losses, 6) if losses else None,
            "win_rate_pct": round(float((pnl_values > 0).mean() * 100), 4)
            if len(pnl_values) else None,
            "tickets": len(replay["trades"]),
            "excursions": excursion_count,
            "side_breakdown": side_breakdown,
        },
        "contract": replay["contract"],
        "counters": replay["counters"],
        "event_counts": replay["event_counts"],
        "trades": replay["trades"],
    }
    result["database"]["sha256"] = hashlib.sha256(replay_db.read_bytes()).hexdigest()
    output_path = Path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "passed": result["passed"], "dataset": result["dataset"],
        "parity": result["parity"], "performance": result["performance"],
        "database": result["database"], "counters": result["counters"],
    }, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
