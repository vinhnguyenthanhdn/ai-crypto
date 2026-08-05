"""Báo cáo hiệu suất Paper Trading (xem plan-02.md mục 2, Success Criteria #4:
"Paper Trading có edge dương sau phí").

Khác Backtest (`scripts/run_backtest.py`, replay dữ liệu lịch sử) — script này
thống kê trên các lệnh ENTRY/EXIT **thật** đã phát sinh qua `run.py` khi chạy
định kỳ (cron/launchd, xem README). Chạy lại script này bất cứ lúc nào để xem
tiến độ tích luỹ, không cần chờ đủ N lệnh.

Usage:
    python scripts/paper_trading_report.py [--no-mlflow]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import state_store, experiment  # noqa: E402
from src.backtest.engine import compute_stats  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-mlflow", action="store_true", help="Bỏ qua log MLflow, chỉ in báo cáo")
    args = parser.parse_args()

    entry_events = state_store.get_events(event_type="ENTRY")
    trade_ids = [e["trade_id"] for e in entry_events if e["trade_id"]]
    summaries = [state_store.get_trade_summary(tid) for tid in trade_ids]
    summaries = [s for s in summaries if s]

    closed = [s for s in summaries if s["status"] == "CLOSED"]
    open_trades = [s for s in summaries if s["status"] == "OPEN"]
    pnl_pcts = [s["pnl_pct"] for s in closed if s.get("pnl_pct") is not None]

    stats = compute_stats(pnl_pcts, init_cash=100.0)  # init_cash chỉ để tính equity curve tương đối theo %

    print(f"Tổng số lệnh đã mở: {len(summaries)}")
    print(f"Đã đóng: {len(closed)}, đang mở: {len(open_trades)}")
    if not closed:
        print("Chưa có lệnh nào đóng — chưa đủ dữ liệu để đánh giá edge.")
        return

    print(f"Win rate: {stats['win_rate_pct']}%")
    print(f"Total return (cộng dồn theo %, chưa tính vốn thật): {stats['total_return_pct']}%")
    print(f"Max drawdown: {stats['max_drawdown_pct']}%")
    print(f"Sharpe (xấp xỉ per-trade): {stats['sharpe_ratio']}")

    if not args.no_mlflow:
        run_id = experiment.log_paper_trading_run(stats, n_trades=len(closed))
        print(f"MLflow run_id: {run_id}", file=sys.stderr)


if __name__ == "__main__":
    main()
