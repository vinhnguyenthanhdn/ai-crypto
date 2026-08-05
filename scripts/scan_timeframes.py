"""Quét đa timeframe — tìm khung "đủ tự tin để đưa vào live".

Với mỗi khung trong danh sách, tự calibrate ngưỡng BUY/WATCH theo phân phối điểm
riêng của khung đó (percentile — vì điểm tối đa backtest đạt được không đổi theo
khung nhưng phân phối thực tế khác nhau, xem `src/backtest/engine.py`), rồi tái
dùng các hàm đo trong `diagnose_backtest.py` (signal edge, cost-gated trade run,
walk-forward theo idx_range) để chấm từng khung theo tiêu chí "đủ tự tin":
>=30 lệnh sau cost gate, |gross_t_stat|>=1.5, total return net >0, walk-forward
dương ở >=2 giai đoạn. Khung đạt đủ 4 tiêu chí trở thành ứng viên đưa vào tổ
hợp confluence đa khung — script này KHÔNG chọn 1 khung "vô địch", chỉ lọc
ứng viên.

Usage:
    python3 scripts/scan_timeframes.py --days 180 --side long
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402
from src.indicators import technical  # noqa: E402
from diagnose_backtest import (  # noqa: E402
    load_ohlcv, score_all_bars, signal_edge_report, run_instrumented, CACHE_DIR,
)

DEFAULT_TIMEFRAMES = ["5m", "15m", "30m", "1h", "2h", "4h"]
BUY_PERCENTILE = 90
WATCH_PERCENTILE = 75
MIN_TRADES = 30
MIN_ABS_T = 1.5


def calibrate_thresholds(scores: pd.DataFrame) -> tuple[float, float]:
    """Ngưỡng riêng theo phân phối điểm của từng khung — ngưỡng BUY/WATCH phải
    calibrate riêng theo từng khung, không dùng chung 1 số."""
    buy = float(np.percentile(scores["total_score"], BUY_PERCENTILE))
    watch = float(np.percentile(scores["total_score"], WATCH_PERCENTILE))
    if watch >= buy:
        watch = buy - 1.0
    return round(buy, 2), round(watch, 2)


def evaluate_criteria(full_result: dict, walk_forward: dict) -> tuple[dict, bool]:
    n_trades = full_result.get("n_trades", 0)
    t_stat = full_result.get("gross_t_stat")
    net_total = full_result.get("pnl_net_pct_total")
    wf_positive = sum(
        1 for p in walk_forward.values()
        if (p.get("pnl_net_pct_total") or 0) > 0 and (p.get("n_trades") or 0) > 0
    )
    checks = {
        "n_trades_ge_30": n_trades >= MIN_TRADES,
        "t_stat_ge_1_5": t_stat is not None and abs(t_stat) >= MIN_ABS_T,
        "net_total_positive": net_total is not None and net_total > 0,
        "walk_forward_ge_2_positive": wf_positive >= 2,
    }
    return checks, all(checks.values())


def scan_one(symbol: str, timeframe: str, days: int, side: str, exchange: str,
             walk_forward_splits: int, refresh: bool) -> dict:
    df = load_ohlcv(symbol, timeframe, days, exchange, refresh)
    print(f"[{timeframe}] {len(df)} bar, {df['ts'].iloc[0]} -> {df['ts'].iloc[-1]}", file=sys.stderr)
    enriched = technical.add_indicators(df)
    scores = score_all_bars(enriched, side)
    buy_threshold, watch_threshold = calibrate_thresholds(scores)
    print(f"[{timeframe}] calibrate: buy={buy_threshold} (p{BUY_PERCENTILE}), "
          f"watch={watch_threshold} (p{WATCH_PERCENTILE})", file=sys.stderr)

    full_a = signal_edge_report(df, scores, side, buy_threshold, watch_threshold)
    full = run_instrumented(df, enriched, scores, side, timeframe, buy_threshold, watch_threshold,
                            exit_mode="full", cost_gate=True)

    n = len(df)
    idx_min, idx_max = int(scores.index.min()), int(scores.index.max())
    total_range = idx_max - idx_min + 1
    bounds = [idx_min + round(total_range * k / walk_forward_splits) for k in range(walk_forward_splits + 1)]
    walk_forward = {}
    for seg in range(walk_forward_splits):
        seg_lo, seg_hi = bounds[seg], bounds[seg + 1]
        seg_a = signal_edge_report(df, scores, side, buy_threshold, watch_threshold, idx_range=(seg_lo, seg_hi))
        seg_r = run_instrumented(df, enriched, scores, side, timeframe, buy_threshold, watch_threshold,
                                 exit_mode="full", cost_gate=True, idx_range=(seg_lo, seg_hi))
        walk_forward[f"period_{seg + 1}"] = {
            "from": str(df["ts"].iloc[min(seg_lo, n - 1)]), "to": str(df["ts"].iloc[min(seg_hi, n - 1)]),
            "n_trades": seg_r.get("n_trades", 0),
            "n_skipped_cost_gate": seg_r.get("n_skipped_cost_gate"),
            "gross_t_stat": seg_r.get("gross_t_stat"),
            "pnl_net_pct_total": seg_r.get("pnl_net_pct_total"),
            "pnl_net_pct_winrate": seg_r.get("pnl_net_pct_winrate"),
            "signal_edge_h24_t_stat": seg_a["horizons"].get(24, {}).get("t_stat_vs_baseline"),
            "signal_edge_h24_edge_pct": seg_a["horizons"].get(24, {}).get("edge_pct"),
        }

    checks, ready = evaluate_criteria(full, walk_forward)
    trades = full.pop("trades", [])
    return {
        "timeframe": timeframe, "side": side, "bars": n,
        "from": str(df["ts"].iloc[0]), "to": str(df["ts"].iloc[-1]),
        "buy_threshold": buy_threshold, "watch_threshold": watch_threshold,
        "score_distribution": {
            "mean": round(float(scores["total_score"].mean()), 2),
            "max": round(float(scores["total_score"].max()), 2),
            f"p{WATCH_PERCENTILE}": watch_threshold, f"p{BUY_PERCENTILE}": buy_threshold,
        },
        "signal_edge_full_period_h24": full_a["horizons"].get(24, {}),
        "full_period": full,
        "walk_forward": walk_forward,
        "criteria_checks": checks,
        "ready": ready,
        "_detail_trades": trades,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default=config.SYMBOL)
    p.add_argument("--timeframes", default=",".join(DEFAULT_TIMEFRAMES))
    p.add_argument("--days", type=int, default=180)
    p.add_argument("--side", choices=["long", "short"], default="long")
    p.add_argument("--exchange", default=config.EXCHANGE_ID)
    p.add_argument("--walk-forward-splits", type=int, default=2)
    p.add_argument("--refresh", action="store_true", help="Bỏ cache, tải lại OHLCV")
    args = p.parse_args()

    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    results = []
    for tf in timeframes:
        print(f"\n===== Quét {tf} ({args.days} ngày) =====", file=sys.stderr)
        r = scan_one(args.symbol, tf, args.days, args.side, args.exchange, args.walk_forward_splits, args.refresh)
        trades = r.pop("_detail_trades")
        results.append(r)

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        prefix = f"scan_{args.exchange}_{args.side}_{tf}_{args.days}d"
        (CACHE_DIR / f"{prefix}.json").write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
        if trades:
            pd.DataFrame(trades).to_csv(CACHE_DIR / f"{prefix}_trades.csv", index=False)

        status = "ĐẠT — ứng viên" if r["ready"] else "chưa đạt"
        fp = r["full_period"]
        print(f"[{tf}] buy={r['buy_threshold']} watch={r['watch_threshold']} "
              f"n_trades={fp.get('n_trades', 0)} t={fp.get('gross_t_stat')} "
              f"net_total={fp.get('pnl_net_pct_total')} -> {status}", file=sys.stderr)

    print("\n===== TỔNG HỢP =====", file=sys.stderr)
    summary = [{
        "timeframe": r["timeframe"], "buy_threshold": r["buy_threshold"], "watch_threshold": r["watch_threshold"],
        "n_trades": r["full_period"].get("n_trades", 0), "gross_t_stat": r["full_period"].get("gross_t_stat"),
        "net_total_pct": r["full_period"].get("pnl_net_pct_total"),
        "net_winrate_pct": r["full_period"].get("pnl_net_pct_winrate"),
        "checks": r["criteria_checks"], "ready": r["ready"],
    } for r in results]
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
