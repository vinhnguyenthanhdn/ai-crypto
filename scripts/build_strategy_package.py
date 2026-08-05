"""CLI đóng gói Strategy Package từ Entry Model đã train + (tuỳ chọn) kết quả
Backtest/Paper Trading gần nhất.

Usage:
    python scripts/build_strategy_package.py <name> --model-version <version> \
        [--backtest-json path/to/backtest_result.json]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import strategy_package, experiment, champion_challenger as cc  # noqa: E402
from src.backtest.engine import compute_stats  # noqa: E402
from src import state_store  # noqa: E402


def _get_model_version_metrics(version: str) -> dict:
    client = cc.get_client()
    mv = client.get_model_version(experiment.ENTRY_MODEL_REGISTRY_NAME, version)
    run = client.get_run(mv.run_id)
    return dict(run.data.metrics)


def _current_paper_stats():
    entry_events = state_store.get_events(event_type="ENTRY")
    trade_ids = [e["trade_id"] for e in entry_events if e["trade_id"]]
    summaries = [state_store.get_trade_summary(tid) for tid in trade_ids]
    closed = [s for s in summaries if s and s["status"] == "CLOSED"]
    pnl_pcts = [s["pnl_pct"] for s in closed if s.get("pnl_pct") is not None]
    if not pnl_pcts:
        return None
    stats = compute_stats(pnl_pcts, init_cash=100.0)
    stats["n_trades"] = len(closed)
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("name")
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--backtest-json", default=None, help="Path tới output JSON của scripts/run_backtest.py")
    args = parser.parse_args()

    metrics = _get_model_version_metrics(args.model_version)

    backtest_result = None
    if args.backtest_json:
        backtest_result = json.loads(Path(args.backtest_json).read_text(encoding="utf-8"))

    paper_stats = _current_paper_stats()

    manifest = strategy_package.build_manifest(
        args.name,
        entry_model_version=args.model_version,
        entry_model_metrics=metrics,
        backtest_result=backtest_result,
        paper_stats=paper_stats,
    )
    manifest_path, model_card_path = strategy_package.save(manifest)
    print(f"Manifest: {manifest_path}")
    print(f"Model Card: {model_card_path}")


if __name__ == "__main__":
    main()
