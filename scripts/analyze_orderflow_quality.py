"""Theo dõi độ ổn định WS CVD — `TODO-ORDERFLOW-QUALITY`. Đọc
`feature_snapshot` (ghi mỗi lần `run.py` chạy, không chỉ lúc có trade) và tính
% các lần chạy dùng được CVD thật từ WebSocket (`collector_ws.py`) so với phải
fallback về REST snapshot xấp xỉ (`order_flow.raw.cvd_source`).

Đây là công cụ đo — KHÔNG tự quyết định đổi trọng số `config.WEIGHTS`. Trọng
số Order Flow hiện tại giả định chất lượng CVD tốt (xem docs/decisions.md mục
"Decision Engine — trọng số theo layer"); nếu % websocket thấp/không ổn định
theo thời gian, cần NGƯỜI quyết định có hạ trọng số layer này không, không tự
sửa `config.WEIGHTS` từ kết quả script này.

Usage:
    python scripts/analyze_orderflow_quality.py [--limit 5000] [--db-path PATH]
"""
import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, state_store  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--db-path", default=None)
    args = parser.parse_args()

    if args.db_path:
        config.DB_PATH = Path(args.db_path)

    snapshots = state_store.get_feature_snapshots(limit=args.limit)
    print(f"Số feature_snapshot đọc được: {len(snapshots)} (DB: {config.DB_PATH})")
    if not snapshots:
        print("CHƯA CÓ DỮ LIỆU — cần chạy run.py/run_paper.sh trước.")
        sys.exit(0)

    by_day = defaultdict(lambda: {"websocket": 0, "rest_snapshot": 0, "unknown": 0})
    total_counts = {"websocket": 0, "rest_snapshot": 0, "unknown": 0}
    for s in snapshots:
        source = s["features"].get("order_flow", {}).get("cvd_source", "unknown")
        if source not in ("websocket", "rest_snapshot"):
            source = "unknown"
        day = s["ts"][:10]
        by_day[day][source] += 1
        total_counts[source] += 1

    n = len(snapshots)
    ws_pct = total_counts["websocket"] / n * 100
    print(f"\nTổng: websocket={total_counts['websocket']} ({ws_pct:.1f}%), "
          f"rest_snapshot={total_counts['rest_snapshot']}, unknown={total_counts['unknown']}")

    print("\nTheo ngày:")
    for day in sorted(by_day.keys()):
        c = by_day[day]
        day_n = sum(c.values())
        print(f"  {day}: websocket={c['websocket']}/{day_n} ({c['websocket']/day_n*100:.0f}%)")

    n_days = len(by_day)
    if n_days < 7:
        print(
            f"\nCHỈ CÓ {n_days} NGÀY DỮ LIỆU — cần theo dõi liên tục lâu hơn (khuyến nghị "
            f"≥ 2-4 tuần chạy ổn định) trước khi kết luận WS CVD đủ tin cậy để xem lại "
            f"trọng số Order Flow trong config.WEIGHTS. Chưa đủ căn cứ ra quyết định."
        )
    elif ws_pct >= 95:
        print(f"\nWS CVD ổn định ({ws_pct:.1f}% qua {n_days} ngày) — có căn cứ để NGƯỜI xem lại "
              f"trọng số Order Flow nếu muốn (không tự đổi config.WEIGHTS ở đây).")
    else:
        print(f"\nWS CVD CHƯA ổn định ({ws_pct:.1f}% qua {n_days} ngày, còn fallback REST) — "
              f"chưa nên tăng trọng số Order Flow dựa trên giả định chất lượng data tốt hơn.")


if __name__ == "__main__":
    main()
