"""Công cụ chẩn đoán Backtest: tách bạch nguyên nhân thua lỗ thành 3 nguồn độc lập
(xem docs/research-technical-signal-edge.md).

Backtest thường chỉ trả về 1 con số win rate — không cho biết lỗ đến từ tín hiệu
vào lệnh sai, rule thoát lệnh cắt quá sớm, hay chi phí giao dịch. Script này chạy
4 nhóm kiểm tra tách rời:

  A. Edge thuần của tín hiệu — forward return sau N bar kể từ lúc tín hiệu bắn,
     KHÔNG áp exit rule, KHÔNG trừ phí. So với baseline toàn bộ bar (unconditional).
     Đây là phép đo duy nhất trả lời "tín hiệu có dự báo được hướng giá không".
  B. Ảnh hưởng của exit rule — chạy engine thật với từng cấu hình exit
     (đủ rule / chỉ SL-TP / thoát cố định sau N bar) trên cùng tập entry.
  C. Ảnh hưởng của chi phí — cùng tập lệnh, tính pnl ở 3 mức: gross (không phí,
     không slippage) / chỉ slippage / đủ phí.
  D. Baseline ngẫu nhiên — entry random cùng số lệnh, cùng exit rule, để biết
     kết quả của tín hiệu có khác gì so với vào lệnh mù.

Log chi tiết từng lệnh (MFE/MAE, ATR lúc entry, score, regime, hold) ghi ra CSV
để soi tay.

Usage:
    python scripts/diagnose_backtest.py --days 30 --buy-threshold 55
    python scripts/diagnose_backtest.py --days 30 --buy-threshold 55 --side short
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402
from src.data import market  # noqa: E402
from src.indicators import technical  # noqa: E402
from src.engine import regime as regime_engine  # noqa: E402
from src.engine import decision, risk  # noqa: E402
from src.backtest.engine import WARMUP_BARS, NEUTRAL_SCORE, DEFAULT_FEE_PCT, DEFAULT_SLIPPAGE_PCT, _timeframe_minutes  # noqa: E402

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "diagnostics"
HORIZONS = [1, 2, 3, 6, 12, 24, 48, 96]  # số bar


# ---------------------------------------------------------------- data & scoring

def load_ohlcv(symbol: str, timeframe: str, days: int, exchange_id: str, refresh: bool) -> pd.DataFrame:
    """Cache OHLCV ra CSV để các lần chạy chẩn đoán dùng CHUNG một tập dữ liệu —
    nếu mỗi lần fetch lại thì cửa sổ thời gian trượt, không so sánh được các kịch bản."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"ohlcv_{exchange_id}_{symbol.replace('/', '')}_{timeframe}_{days}d.csv"
    if cache.exists() and not refresh:
        print(f"[data] dùng cache {cache.name}", file=sys.stderr)
        return pd.read_csv(cache, parse_dates=["ts"])

    exchange = market.get_binance_exchange() if exchange_id == "binance" else market.get_exchange()
    print(f"[data] tải {symbol} {timeframe} {days} ngày từ {exchange_id}...", file=sys.stderr)
    df = technical.to_dataframe(market.fetch_historical_ohlcv(exchange, symbol, timeframe, days))
    df.to_csv(cache, index=False)
    return df


def score_all_bars(enriched: pd.DataFrame, side: str) -> pd.DataFrame:
    """Điểm số + regime cho mọi bar sau warmup. Tính 1 lần, tái dùng cho mọi kịch bản."""
    score_fn = technical.score_from_indicators if side == "long" else technical.score_short_from_indicators
    rows = []
    for i in range(WARMUP_BARS, len(enriched)):
        tech = score_fn(enriched, idx=i)
        reg = regime_engine.classify_regime(enriched, idx=i)
        total = decision.compute_total_score({
            "technical": tech["total"], "order_flow": NEUTRAL_SCORE, "derivatives": NEUTRAL_SCORE,
            "cross_market": NEUTRAL_SCORE, "sentiment": NEUTRAL_SCORE, "regime": reg["score"],
        })
        rows.append({
            "idx": i, "total_score": total, "tech_score": tech["total"],
            "regime": reg["label"], **{f"sig_{k}": v for k, v in tech["breakdown"].items()},
        })
    return pd.DataFrame(rows).set_index("idx")


# ------------------------------------------------------- A. edge thuần của tín hiệu

def signal_edge_report(df: pd.DataFrame, scores: pd.DataFrame, side: str,
                       buy_threshold: float, watch_threshold: float) -> dict:
    """Forward return từ giá open bar i+1 (đúng điểm fill của engine) sang open bar
    i+1+h, không exit rule, không chi phí. So tín hiệu vs toàn bộ bar."""
    open_vals = df["open"].to_numpy()
    n = len(df)
    sign = 1.0 if side == "long" else -1.0

    entry_fn = decision.decide_entry if side == "long" else decision.decide_short_entry
    want_action = "BUY" if side == "long" else "SHORT"
    fired = []
    for idx, row in scores.iterrows():
        action, _ = entry_fn(
            row["total_score"], row["regime"], trading_halted=False,
            buy_threshold=buy_threshold, watch_threshold=watch_threshold,
        )
        if action == want_action:
            fired.append(idx)

    def fwd(idxs, h):
        out = []
        for i in idxs:
            if i + 1 + h < n:
                out.append(sign * (open_vals[i + 1 + h] / open_vals[i + 1] - 1) * 100)
        return np.array(out)

    all_idxs = scores.index.to_numpy()
    report = {"n_signal_bars": len(fired), "n_all_bars": len(all_idxs), "horizons": {}}
    for h in HORIZONS:
        sig, base = fwd(fired, h), fwd(all_idxs, h)
        if len(sig) < 2:
            continue
        # t-test 2 mẫu (Welch) — tín hiệu có khác baseline một cách có ý nghĩa không
        se = np.sqrt(sig.var(ddof=1) / len(sig) + base.var(ddof=1) / len(base))
        t = (sig.mean() - base.mean()) / se if se > 0 else 0.0
        report["horizons"][h] = {
            "signal_mean_pct": round(float(sig.mean()), 4),
            "baseline_mean_pct": round(float(base.mean()), 4),
            "edge_pct": round(float(sig.mean() - base.mean()), 4),
            "signal_hit_rate_pct": round(float((sig > 0).mean() * 100), 2),
            "baseline_hit_rate_pct": round(float((base > 0).mean() * 100), 2),
            "t_stat_vs_baseline": round(float(t), 2),
            "signal_std_pct": round(float(sig.std(ddof=1)), 4),
        }
    return report


# --------------------------------------------------- B/C/D. engine có instrument

def run_instrumented(df: pd.DataFrame, enriched: pd.DataFrame, scores: pd.DataFrame, side: str,
                     timeframe: str, buy_threshold: float, watch_threshold: float,
                     exit_mode: str = "full", fixed_hold_bars: int = 0,
                     fee_pct: float = DEFAULT_FEE_PCT, slippage_pct: float = DEFAULT_SLIPPAGE_PCT,
                     random_entry_seed: int | None = None, n_random_trades: int = 0,
                     cost_gate: bool = False) -> dict:
    """Bản sao engine thật nhưng ghi log đầy đủ + cho phép thay đổi cơ chế thoát.

    exit_mode: full (đủ rule) | sltp (chỉ SL/TP) | fixed (thoát sau `fixed_hold_bars` bar).
    random_entry_seed: nếu set, bỏ qua tín hiệu và vào lệnh ở các bar ngẫu nhiên.
    cost_gate: bật ràng buộc `TP_distance >= MIN_TP_COST_RATIO * chi phí khứ hồi`
        (mục 6.1) — tắt mặc định để các phép đo A/B/D phản ánh hành vi trước khi sửa.
    """
    n = len(df)
    open_vals, close_vals = df["open"].to_numpy(), df["close"].to_numpy()
    high_vals, low_vals = df["high"].to_numpy(), df["low"].to_numpy()
    ts_vals = df["ts"].to_numpy() if "ts" in df.columns else np.arange(n)
    cooldown_bars = max(1, round(config.COOLDOWN_MINUTES / _timeframe_minutes(timeframe)))
    min_hold_bars = max(1, round(config.MIN_HOLD_MINUTES / _timeframe_minutes(timeframe)))

    exit_fn = decision.decide_exit if side == "long" else decision.decide_short_exit
    plan_fn = risk.compute_position_plan if side == "long" else risk.compute_short_position_plan
    pnl_fn = risk.compute_pnl_pct if side == "long" else risk.compute_short_pnl_pct
    want_action = "BUY" if side == "long" else "SHORT"
    entry_fn = decision.decide_entry if side == "long" else decision.decide_short_entry

    random_bars = set()
    if random_entry_seed is not None:
        rng = np.random.default_rng(random_entry_seed)
        pool = np.arange(WARMUP_BARS, n - 1)
        random_bars = set(rng.choice(pool, size=min(n_random_trades * 4, len(pool)), replace=False).tolist())

    trades, position, cooldown_until, n_skipped = [], None, -1, 0
    for i in range(WARMUP_BARS, n - 1):
        fill_price = float(open_vals[i + 1])

        if position is not None:
            held = i - position["entry_idx"]
            if exit_mode == "fixed":
                should_exit = held >= fixed_hold_bars
                reason = f"Thoát cố định sau {fixed_hold_bars} bar" if should_exit else ""
            else:
                should_exit, reason = exit_fn(
                    position, float(close_vals[i]), enriched, idx=i,
                    min_hold_satisfied=(held >= min_hold_bars),
                )
                if exit_mode == "sltp" and should_exit and "stop loss" not in reason and "take profit" not in reason:
                    should_exit, reason = False, ""
            # chốt cưỡng bức ở bar cuối để không bỏ sót lệnh đang mở
            if not should_exit and i == n - 2:
                should_exit, reason = True, "Hết dữ liệu"

            if should_exit:
                e_idx = position["entry_idx"]
                gross_entry, gross_exit = position["fill_price"], fill_price
                exit_price = fill_price * (1 - slippage_pct) if side == "long" else fill_price * (1 + slippage_pct)
                seg_hi = float(high_vals[e_idx:i + 2].max())
                seg_lo = float(low_vals[e_idx:i + 2].min())
                if side == "long":
                    mfe = (seg_hi / gross_entry - 1) * 100
                    mae = (seg_lo / gross_entry - 1) * 100
                else:
                    mfe = (1 - seg_lo / gross_entry) * 100
                    mae = (1 - seg_hi / gross_entry) * 100
                trades.append({
                    "entry_idx": e_idx, "exit_idx": i + 1, "hold_bars": i + 1 - e_idx,
                    "entry_ts": str(ts_vals[e_idx]), "exit_ts": str(ts_vals[i + 1]),
                    "entry_price": round(position["entry_price"], 2), "exit_price": round(exit_price, 2),
                    "pnl_gross_pct": round(pnl_fn(gross_entry, gross_exit), 4),
                    "pnl_after_slip_pct": round(pnl_fn(position["entry_price"], exit_price), 4),
                    "pnl_net_pct": round(pnl_fn(position["entry_price"], exit_price) - fee_pct * 100 * 2, 4),
                    "mfe_pct": round(mfe, 4), "mae_pct": round(mae, 4),
                    "mfe_atr": round(mfe / position["atr_pct"], 3) if position["atr_pct"] else None,
                    "mae_atr": round(mae / position["atr_pct"], 3) if position["atr_pct"] else None,
                    "atr_pct": round(position["atr_pct"], 4),
                    "tp_distance_pct": round(abs(position["take_profit_price"] / position["entry_price"] - 1) * 100, 4),
                    "sl_distance_pct": round(abs(position["stop_price"] / position["entry_price"] - 1) * 100, 4),
                    "total_score": position["total_score"], "regime": position["regime"],
                    "reason": reason,
                })
                cooldown_until = i + 1 + cooldown_bars
                position = None
            continue

        if i < cooldown_until:
            continue

        if random_entry_seed is not None:
            take = i in random_bars and len(trades) < n_random_trades
        else:
            row = scores.loc[i]
            action, _ = entry_fn(row["total_score"], row["regime"], trading_halted=False,
                                 buy_threshold=buy_threshold, watch_threshold=watch_threshold)
            take = action == want_action

        if take:
            atr = float(enriched.iloc[i]["atr"])
            if pd.isna(atr) or atr <= 0:
                continue
            entry_price = fill_price * (1 + slippage_pct) if side == "long" else fill_price * (1 - slippage_pct)
            plan = plan_fn(entry_price, atr, fee_pct=fee_pct, slippage_pct=slippage_pct)
            if cost_gate and not plan["edge_viable"]:
                n_skipped += 1
                continue
            row = scores.loc[i] if random_entry_seed is None else None
            position = {
                "entry_idx": i + 1, "entry_price": entry_price, "fill_price": fill_price,
                "stop_price": plan["stop_price"], "take_profit_price": plan["take_profit_price"],
                "atr_pct": atr / fill_price * 100,
                "total_score": float(row["total_score"]) if row is not None else None,
                "regime": row["regime"] if row is not None else None,
            }

    return {"trades": trades, "n_skipped_cost_gate": n_skipped, **summarize(trades)}


def summarize(trades: list[dict]) -> dict:
    if not trades:
        return {"n_trades": 0}
    d = pd.DataFrame(trades)
    out = {"n_trades": len(d), "avg_hold_bars": round(float(d["hold_bars"].mean()), 2)}
    for col in ["pnl_gross_pct", "pnl_after_slip_pct", "pnl_net_pct"]:
        out[f"{col}_mean"] = round(float(d[col].mean()), 4)
        out[f"{col}_winrate"] = round(float((d[col] > 0).mean() * 100), 2)
        out[f"{col}_total"] = round(float((1 + d[col] / 100).prod() - 1) * 100, 2)
    # t-test 1 mẫu: return gross có khác 0 không
    g = d["pnl_gross_pct"].to_numpy()
    out["gross_t_stat"] = round(float(g.mean() / (g.std(ddof=1) / np.sqrt(len(g)))), 2) if g.std(ddof=1) > 0 else None
    out["avg_mfe_atr"] = round(float(d["mfe_atr"].mean()), 3)
    out["avg_mae_atr"] = round(float(d["mae_atr"].mean()), 3)
    out["pct_reach_tp_distance"] = round(float((d["mfe_pct"] >= d["tp_distance_pct"]).mean() * 100), 2)
    out["pct_reach_sl_distance"] = round(float((d["mae_pct"] <= -d["sl_distance_pct"]).mean() * 100), 2)
    out["exit_reasons"] = d["reason"].str.split("(").str[0].str.strip().value_counts().to_dict()
    return out


# ------------------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default=config.SYMBOL)
    p.add_argument("--timeframe", default=config.TIMEFRAME)
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--side", choices=["long", "short"], default="long")
    p.add_argument("--exchange", default=config.EXCHANGE_ID)
    p.add_argument("--buy-threshold", type=float, default=55.0)
    p.add_argument("--watch-threshold", type=float, default=45.0)
    p.add_argument("--refresh", action="store_true", help="Bỏ cache, tải lại OHLCV")
    p.add_argument("--out-prefix", default=None)
    args = p.parse_args()

    df = load_ohlcv(args.symbol, args.timeframe, args.days, args.exchange, args.refresh)
    print(f"[data] {len(df)} bar, {df['ts'].iloc[0]} -> {df['ts'].iloc[-1]}", file=sys.stderr)
    enriched = technical.add_indicators(df)
    print("[score] tính điểm mọi bar...", file=sys.stderr)
    scores = score_all_bars(enriched, args.side)

    result = {
        "meta": {
            "symbol": args.symbol, "timeframe": args.timeframe, "days": args.days, "side": args.side,
            "exchange": args.exchange, "buy_threshold": args.buy_threshold,
            "bars": len(df), "from": str(df["ts"].iloc[0]), "to": str(df["ts"].iloc[-1]),
            "round_trip_cost_pct": round(DEFAULT_FEE_PCT * 100 * 2 + DEFAULT_SLIPPAGE_PCT * 100 * 2, 3),
            "median_bar_move_pct": round(float((df["close"] / df["open"] - 1).abs().median() * 100), 4),
        },
        "score_distribution": {
            "mean": round(float(scores["total_score"].mean()), 2),
            "max": round(float(scores["total_score"].max()), 2),
            "pct_bars_above_threshold": round(float((scores["total_score"] >= args.buy_threshold).mean() * 100), 2),
            "regime_counts": scores["regime"].value_counts().to_dict(),
        },
    }

    print("[A] edge thuần của tín hiệu (forward return, không exit rule, không phí)...", file=sys.stderr)
    result["A_signal_edge"] = signal_edge_report(df, scores, args.side, args.buy_threshold, args.watch_threshold)

    print("[B] ảnh hưởng của exit rule...", file=sys.stderr)
    variants = {"full": {"exit_mode": "full"}, "sltp_only": {"exit_mode": "sltp"}}
    for h in [3, 6, 12, 24, 48]:
        variants[f"fixed_{h}bar"] = {"exit_mode": "fixed", "fixed_hold_bars": h}
    result["B_exit_variants"] = {}
    detail_trades = None
    for name, kw in variants.items():
        r = run_instrumented(df, enriched, scores, args.side, args.timeframe,
                             args.buy_threshold, args.watch_threshold, **kw)
        if name == "full":
            detail_trades = r["trades"]
        result["B_exit_variants"][name] = {k: v for k, v in r.items() if k != "trades"}

    print("[C] ảnh hưởng chi phí — xem 3 cột pnl_gross/after_slip/net trong mỗi biến thể", file=sys.stderr)
    print("[D] baseline entry ngẫu nhiên...", file=sys.stderr)
    n_ref = result["B_exit_variants"]["full"].get("n_trades", 0)
    rand_runs = []
    for seed in range(5):
        r = run_instrumented(df, enriched, scores, args.side, args.timeframe,
                             args.buy_threshold, args.watch_threshold,
                             exit_mode="full", random_entry_seed=seed, n_random_trades=n_ref)
        rand_runs.append({k: v for k, v in r.items() if k != "trades"})
    result["D_random_baseline"] = {
        "runs": rand_runs,
        "avg_gross_mean": round(float(np.mean([r["pnl_gross_pct_mean"] for r in rand_runs])), 4),
        "avg_net_winrate": round(float(np.mean([r["pnl_net_pct_winrate"] for r in rand_runs])), 2),
        "avg_gross_winrate": round(float(np.mean([r["pnl_gross_pct_winrate"] for r in rand_runs])), 2),
    }

    print("[E] quét ngưỡng cost gate (MIN_TP_COST_RATIO)...", file=sys.stderr)
    original_k = config.MIN_TP_COST_RATIO
    result["E_cost_gate_sweep"] = {}
    for k in [0.0, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]:
        config.MIN_TP_COST_RATIO = k
        for exit_mode, kw in [("full", {"exit_mode": "full"}), ("fixed_24bar", {"exit_mode": "fixed", "fixed_hold_bars": 24})]:
            r = run_instrumented(df, enriched, scores, args.side, args.timeframe,
                                 args.buy_threshold, args.watch_threshold, cost_gate=True, **kw)
            result["E_cost_gate_sweep"][f"k={k}|{exit_mode}"] = {
                k2: v for k2, v in r.items() if k2 != "trades"
            }
    config.MIN_TP_COST_RATIO = original_k

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    prefix = args.out_prefix or f"diag_{args.exchange}_{args.side}_{args.timeframe}_{args.days}d"
    (CACHE_DIR / f"{prefix}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.DataFrame(detail_trades).to_csv(CACHE_DIR / f"{prefix}_trades.csv", index=False)
    print(f"[out] {CACHE_DIR / (prefix + '.json')}", file=sys.stderr)
    print(f"[out] {CACHE_DIR / (prefix + '_trades.csv')}", file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
