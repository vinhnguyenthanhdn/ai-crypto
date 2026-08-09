"""Phân tích tương quan layer-score vs kết quả trade thật —
`TODO-SIX-LAYERS`. Đây là công cụ chỉ dùng sau khi nguồn dữ liệu được xác minh
bất cứ khi nào Feature Store tích luỹ đủ dữ liệu qua `run.py`/`run_paper.sh`
— không "chờ" bằng tay, script tự báo còn thiếu bao nhiêu nếu chưa đủ.

Nguồn dữ liệu: `event_log` (ENTRY ghép EXIT theo `trade_id`) — ENTRY payload
có `layer_scores`/`total_score` thật tại lúc vào lệnh (thêm từ 2026-08-05,
xem `run.py::_handle_entry`) + `feature.order_flow.cvd_source` (xem
`scripts/analyze_orderflow_quality.py` cho câu hỏi #6 liên quan).

Usage:
    python scripts/analyze_layer_signal.py [--min-trades 20] [--db-path PATH]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, state_store  # noqa: E402

LAYERS = ["technical", "order_flow", "derivatives", "cross_market", "sentiment", "regime"]


def _paired_trades():
    """Ghép ENTRY + EXIT theo trade_id — chỉ trả trade đã đóng, có đủ
    layer_scores ở ENTRY (trade mở trước 2026-08-05 không có field này)."""
    entries = {e["trade_id"]: e for e in state_store.get_events(event_type="ENTRY", limit=100000) if e["trade_id"]}
    exits = {e["trade_id"]: e for e in state_store.get_events(event_type="EXIT", limit=100000) if e["trade_id"]}
    paired = []
    for trade_id, entry in entries.items():
        exit_ = exits.get(trade_id)
        if not exit_:
            continue
        layer_scores = entry["payload"].get("layer_scores")
        pnl_pct = exit_["payload"].get("pnl_pct")
        if layer_scores is None or pnl_pct is None:
            continue
        cvd_source = entry["payload"].get("feature", {}).get("order_flow", {}).get("cvd_source")
        paired.append({"trade_id": trade_id, "layer_scores": layer_scores, "pnl_pct": pnl_pct, "cvd_source": cvd_source})
    return paired


def _layer_correlation(trades, layer):
    """Chia trade thành 2 nhóm theo layer-score (>= median / < median), so
    win rate + mean PnL 2 nhóm — không dùng Pearson correlation trực tiếp vì
    số trade nhỏ, so nhóm robust hơn với outlier."""
    scores = [t["layer_scores"].get(layer) for t in trades if t["layer_scores"].get(layer) is not None]
    if len(scores) < 4:
        return None
    scores_sorted = sorted(scores)
    median = scores_sorted[len(scores_sorted) // 2]
    high = [t for t in trades if (t["layer_scores"].get(layer) or 0) >= median]
    low = [t for t in trades if (t["layer_scores"].get(layer) or 0) < median]

    def _stats(group):
        if not group:
            return {"n": 0, "win_rate_pct": None, "mean_pnl_pct": None}
        pnls = [t["pnl_pct"] for t in group]
        wins = sum(1 for p in pnls if p > 0)
        return {"n": len(group), "win_rate_pct": round(wins / len(group) * 100, 1), "mean_pnl_pct": round(sum(pnls) / len(pnls), 3)}

    return {"median_score": median, "high_score_group": _stats(high), "low_score_group": _stats(low)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-trades", type=int, default=20, help="Số trade tối thiểu để phân tích có ý nghĩa")
    parser.add_argument("--db-path", default=None)
    args = parser.parse_args()

    if args.db_path:
        config.DB_PATH = Path(args.db_path)

    trades = _paired_trades()
    n = len(trades)
    print(f"Số trade đã đóng có layer_scores ở ENTRY: {n} (DB: {config.DB_PATH})")

    if n < args.min_trades:
        print(
            f"CHƯA ĐỦ DỮ LIỆU — cần tối thiểu {args.min_trades} trade, hiện có {n}. "
            f"Chạy `run.py`/`run_paper.sh` lâu hơn để tích luỹ thêm (mỗi trade cần Feature "
            f"Store đủ 1 vòng ENTRY+EXIT trọn vẹn). Không suy diễn kết luận từ mẫu quá nhỏ."
        )
        sys.exit(0)

    n_ws_cvd = sum(1 for t in trades if t["cvd_source"] == "websocket")
    print(f"Order Flow dùng WS CVD thật: {n_ws_cvd}/{n} trade ({n_ws_cvd/n*100:.1f}%) — "
          f"còn lại dùng REST snapshot xấp xỉ (xem scripts/analyze_orderflow_quality.py).")

    print("\nTương quan layer-score vs kết quả trade (nhóm >= median so với < median):")
    for layer in LAYERS:
        result = _layer_correlation(trades, layer)
        if result is None:
            print(f"  {layer}: không đủ dữ liệu")
            continue
        high, low = result["high_score_group"], result["low_score_group"]
        print(f"  {layer} (median={result['median_score']}):")
        print(f"    score cao (n={high['n']}): win_rate={high['win_rate_pct']}% mean_pnl={high['mean_pnl_pct']}%")
        print(f"    score thấp (n={low['n']}): win_rate={low['win_rate_pct']}% mean_pnl={low['mean_pnl_pct']}%")


if __name__ == "__main__":
    main()
