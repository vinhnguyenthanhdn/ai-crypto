"""CLI chạy Backtest "paper test" (tick-recompute, hướng tới parity với Paper
Trading — xem `TODO-BACKTEST-PARITY`) trên dữ liệu lịch sử.

Khác `scripts/run_backtest.py` (chấm theo nến đóng): tải thêm 1 khung nhỏ hơn
(`--tick-timeframe`, mặc định 1m — khung nhỏ nhất sàn hỗ trợ, dùng làm proxy
tick giá thật) và tick-recompute Technical + kiểm tra SL/TP mỗi tick trong mỗi
nến chính, chạy liên tục qua toàn bộ khoảng thời gian (không mô phỏng chu kỳ
lịch activation rời rạc như Paper Trading thật).

Usage:
    python scripts/run_paper_backtest.py [--symbol BTC/USDT] [--timeframe 5m] [--days 30]
"""
import argparse
import gzip
import hashlib
import json
import math
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, experiment  # noqa: E402
from src.data import market  # noqa: E402
from src.indicators import technical  # noqa: E402
from src.backtest import paper_engine  # noqa: E402
from src.backtest.engine import WARMUP_BARS, compute_accounting_stats  # noqa: E402


def _split_walk_forward(primary_df, tick_df, funding_rates, n):
    """Chia `primary_df` thành `n` đoạn KHÔNG chồng lấp về phần giao dịch (mỗi
    đoạn vẫn có `WARMUP_BARS` bar lead-in mượn từ đoạn liền trước để indicator
    ổn định ngay từ bar đầu đoạn — không tính lead-in này là trùng lặp vì nó
    chỉ dùng để warmup, không sinh trade). Chống overfit theo 1 giai đoạn thị
    trường cụ thể (xem `TODO-EDGE-WALK-FORWARD`)."""
    total = len(primary_df)
    chunk_len = (total - WARMUP_BARS) // n
    if chunk_len < 10:
        raise ValueError(f"Không đủ bar để chia {n} giai đoạn (tổng {total} bar, cần >= {WARMUP_BARS + n * 10})")

    segments = []
    for i in range(n):
        start = i * chunk_len
        end = start + WARMUP_BARS + chunk_len
        seg_primary = primary_df.iloc[start:end].reset_index(drop=True)
        ts_lo, ts_hi = seg_primary["ts"].iloc[0], seg_primary["ts"].iloc[-1]

        seg_tick = None
        if tick_df is not None:
            mask = (tick_df["ts"] >= ts_lo) & (tick_df["ts"] <= ts_hi)
            seg_tick = tick_df[mask].reset_index(drop=True)

        seg_funding = None
        if funding_rates:
            lo_ms, hi_ms = int(ts_lo.timestamp() * 1000), int(ts_hi.timestamp() * 1000)
            seg_funding = [f for f in funding_rates if lo_ms <= f["timestamp"] <= hi_ms]

        segments.append((seg_primary, seg_tick, seg_funding))
    return segments


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=config.SYMBOL)
    parser.add_argument("--timeframe", default=config.TIMEFRAME)
    parser.add_argument("--tick-timeframe", default="1m", help="Khung nhỏ hơn dùng làm proxy tick giá thật")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--out", default=None, help="Ghi kết quả JSON ra file, mặc định chỉ in ra stdout")
    parser.add_argument(
        "--buy-threshold", type=float, default=None,
        help="Ngưỡng BUY riêng cho backtest (xem giới hạn toán học trong engine.py) — mặc định dùng BUY_SCORE_THRESHOLD sống",
    )
    parser.add_argument("--watch-threshold", type=float, default=None)
    parser.add_argument(
        "--sl-tp-mode", default="atr", choices=["atr", "structural"],
        help="atr (mặc định) hoặc structural provisional (chỉ side=long)",
    )
    parser.add_argument(
        "--side", default="long", choices=["long", "short"],
        help="long (mặc định) hoặc short provisional",
    )
    parser.add_argument(
        "--market-type", default=config.MARKET_TYPE, choices=["spot", "swap"],
        help="swap: fetch contract symbol + funding; chỉ dùng sau TODO-SWAP-PARITY",
    )
    parser.add_argument("--no-mlflow", action="store_true", help="Bỏ qua log MLflow, chỉ in kết quả")
    parser.add_argument(
        "--scoring-profile", default=config.SCORING_PROFILE,
        choices=["no_trade", "champion", "support_resistance_only"],
    )
    parser.add_argument("--sr-required-swings", type=int, default=config.SR_REQUIRED_SWINGS)
    parser.add_argument("--since", default=None, help="ISO timestamp inclusive sau khi tải dữ liệu")
    parser.add_argument("--until", default=None, help="ISO timestamp exclusive; dùng tạo holdout cố định")
    parser.add_argument(
        "--dataset-cache", default=None,
        help="File .json.gz chứa raw primary/tick/funding; tạo nếu chưa có, tái dùng nếu đã có",
    )
    parser.add_argument(
        "--walk-forward", type=int, default=1,
        help="Chia dữ liệu thành N giai đoạn KHÔNG chồng lấp, chạy backtest độc lập từng giai đoạn "
        "(chống overfit theo 1 giai đoạn thị trường) — mặc định 1 (không chia)",
    )
    parser.add_argument(
        "--include-timeline", action="store_true",
        help="Ghi chuỗi score/giá/action từng tick-proxy vào artifact để xem trên dashboard",
    )
    args = parser.parse_args()

    config.MARKET_TYPE = args.market_type
    exchange_symbol = market.resolve_symbol(args.symbol, args.market_type)

    cache_path = Path(args.dataset_cache) if args.dataset_cache else None
    if cache_path and cache_path.exists():
        with gzip.open(cache_path, "rt", encoding="utf-8") as fh:
            cached = json.load(fh)
        expected = (args.symbol, args.market_type, args.timeframe, args.tick_timeframe)
        actual = tuple(cached["metadata"][k] for k in ("symbol", "market_type", "timeframe", "tick_timeframe"))
        if actual != expected:
            raise ValueError(f"Dataset cache metadata không khớp: {actual} != {expected}")
        raw_primary, raw_tick = cached["primary"], cached["tick"]
        funding_rates = cached.get("funding")
        print(f"Đã đọc dataset cache {cache_path}", file=sys.stderr)
    else:
        exchange = market.get_exchange()
        print(f"Đang tải OHLCV chính {exchange_symbol} {args.timeframe} — {args.days} ngày gần nhất...", file=sys.stderr)
        raw_primary = market.fetch_historical_ohlcv(exchange, exchange_symbol, args.timeframe, args.days)
        if not raw_primary:
            print("Không lấy được dữ liệu OHLCV khung chính.", file=sys.stderr)
            sys.exit(1)

        print(f"Đang tải OHLCV tick-proxy {exchange_symbol} {args.tick_timeframe} — {args.days} ngày gần nhất...", file=sys.stderr)
        raw_tick = market.fetch_historical_ohlcv(exchange, exchange_symbol, args.tick_timeframe, args.days)
        if not raw_tick:
            print("Không lấy được dữ liệu OHLCV tick-proxy — chạy fallback không tick (giống engine.py cũ).", file=sys.stderr)

        funding_rates = None
        if args.market_type == "swap":
            print(f"Đang tải lịch sử funding rate {exchange_symbol} — {args.days} ngày gần nhất...", file=sys.stderr)
            funding_rates = market.fetch_historical_funding_rates(exchange, exchange_symbol, args.days)
            print(f"Có {len(funding_rates)} funding event.", file=sys.stderr)
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(cache_path, "wt", encoding="utf-8") as fh:
                json.dump({
                    "metadata": {
                        "symbol": args.symbol, "market_type": args.market_type,
                        "timeframe": args.timeframe, "tick_timeframe": args.tick_timeframe,
                    },
                    "primary": raw_primary, "tick": raw_tick, "funding": funding_rates,
                }, fh, separators=(",", ":"))
            print(f"Đã ghi dataset cache {cache_path}", file=sys.stderr)

    primary_df = technical.to_dataframe(raw_primary)
    tick_df = technical.to_dataframe(raw_tick) if raw_tick else None

    # Không dùng nến cuối đang hình thành. CCXT timestamp là thời điểm mở nến.
    def _closed_only(df, timeframe):
        if df is None or df.empty:
            return df
        unit, value = timeframe[-1], int(timeframe[:-1])
        minutes = {"m": value, "h": value * 60, "d": value * 1440}[unit]
        now = pd.Timestamp.now(tz="UTC").tz_localize(None)
        return df[df["ts"] + pd.Timedelta(minutes=minutes) <= now].reset_index(drop=True)

    primary_df = _closed_only(primary_df, args.timeframe)
    tick_df = _closed_only(tick_df, args.tick_timeframe)
    if args.since:
        since = pd.Timestamp(args.since).tz_localize(None) if pd.Timestamp(args.since).tzinfo else pd.Timestamp(args.since)
        primary_df = primary_df[primary_df["ts"] >= since].reset_index(drop=True)
        tick_df = tick_df[tick_df["ts"] >= since].reset_index(drop=True) if tick_df is not None else None
    if args.until:
        until = pd.Timestamp(args.until).tz_localize(None) if pd.Timestamp(args.until).tzinfo else pd.Timestamp(args.until)
        primary_df = primary_df[primary_df["ts"] < until].reset_index(drop=True)
        tick_df = tick_df[tick_df["ts"] < until].reset_index(drop=True) if tick_df is not None else None

    def _dataset_hash(df):
        if df is None:
            return None
        values = pd.util.hash_pandas_object(df, index=False).values.tobytes()
        return hashlib.sha256(values).hexdigest()

    manifest = {
        "experiment_id": config.SR_EXPERIMENT_ID if args.scoring_profile == "support_resistance_only" else None,
        "scoring_profile": args.scoring_profile,
        "strategy_package_id": config.STRATEGY_PACKAGE_ID,
        "feature_version": config.FEATURE_VERSION,
        "engine_version": config.PAPER_ENGINE_VERSION,
        "source_exchange": config.EXCHANGE_ID,
        "symbol": args.symbol,
        "market_type": args.market_type,
        "timeframe": args.timeframe,
        "tick_timeframe": args.tick_timeframe,
        "dataset": {
            "primary_hash": _dataset_hash(primary_df),
            "tick_hash": _dataset_hash(tick_df),
            "start": str(primary_df["ts"].iloc[0]) if len(primary_df) else None,
            "end": str(primary_df["ts"].iloc[-1]) if len(primary_df) else None,
        },
        "cost": {"fee_pct": config.FEE_PCT, "slippage_pct": config.SLIPPAGE_PCT},
        "execution": {
            "fill_assumption": "subbar_ohlc_adverse_first",
            "end_of_data_policy": "close_at_last_closed_bar",
            "max_concurrent_positions": 1,
            "cooldown_minutes": config.COOLDOWN_MINUTES,
            "max_hold_minutes": config.MAX_HOLD_MINUTES,
            "candle_policy": "closed_primary_and_tick_bars_only",
        },
        "sr": {
            **config.support_resistance_manifest(),
            "required_swings": args.sr_required_swings,
            "decision_threshold": args.buy_threshold or config.SR_DECISION_THRESHOLD,
        },
    }
    print(f"Có {len(primary_df)} bar chính, {len(tick_df) if tick_df is not None else 0} bar tick-proxy. Đang chạy backtest...", file=sys.stderr)

    common_kwargs = dict(
        symbol=args.symbol, timeframe=args.timeframe, tick_timeframe=args.tick_timeframe,
        buy_threshold=args.buy_threshold, watch_threshold=args.watch_threshold,
        sl_tp_mode=args.sl_tp_mode, side=args.side,
        scoring_profile=args.scoring_profile, sr_required_swings=args.sr_required_swings,
        collect_timeline=args.include_timeline,
    )

    if args.walk_forward > 1:
        segments = _split_walk_forward(primary_df, tick_df, funding_rates, args.walk_forward)
        seg_results = []
        for i, (seg_primary, seg_tick, seg_funding) in enumerate(segments):
            print(f"--- Giai đoạn {i + 1}/{args.walk_forward}: {seg_primary['ts'].iloc[0]} -> "
                  f"{seg_primary['ts'].iloc[-1]} ({len(seg_primary)} bar) ---", file=sys.stderr)
            r = paper_engine.run_paper_backtest(seg_primary, seg_tick, funding_rates=seg_funding, **common_kwargs)
            r["period"] = {
                "warmup_start": str(seg_primary["ts"].iloc[0]),
                "trade_start": str(seg_primary["ts"].iloc[WARMUP_BARS]),
                "end": str(seg_primary["ts"].iloc[-1]),
            }
            seg_results.append(r)
        all_trades = sorted(
            (trade for segment in seg_results for trade in segment["trades"]),
            key=lambda trade: trade["entry_time"],
        )
        wins = sum(1 for trade in all_trades if trade["accounting"]["net_pnl_usd"] > 0)
        total = len(all_trades)
        if total:
            p = wins / total
            z = 1.96
            denom = 1 + z * z / total
            center = (p + z * z / (2 * total)) / denom
            margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
            ci95 = [round(max(0, center - margin) * 100, 4), round(min(1, center + margin) * 100, 4)]
        else:
            ci95 = None
        win_pnls = [t["accounting"]["net_pnl_usd"] for t in all_trades if t["accounting"]["net_pnl_usd"] > 0]
        loss_pnls = [-t["accounting"]["net_pnl_usd"] for t in all_trades if t["accounting"]["net_pnl_usd"] < 0]
        avg_win = sum(win_pnls) / len(win_pnls) if win_pnls else None
        avg_loss = sum(loss_pnls) / len(loss_pnls) if loss_pnls else None
        break_even = (
            avg_loss / (avg_win + avg_loss) * 100
            if avg_win is not None and avg_loss is not None and avg_win + avg_loss > 0 else None
        )
        result = {
            "walk_forward_segments": seg_results,
            "walk_forward_summary": {
                **compute_accounting_stats(all_trades, config.ACCOUNT_EQUITY_USD),
                "n_trades": total,
                "profitable_segments": sum(1 for s in seg_results if s["net"]["total_pnl_usd"] > 0),
                "n_segments": len(seg_results),
                "win_rate_ci95_pct": ci95,
                "break_even_win_rate_pct": round(break_even, 4) if break_even is not None else None,
                "equity_policy": "segment trades concatenated; each segment sizes from initial equity",
            },
            "market_type": args.market_type,
        }
    else:
        result = paper_engine.run_paper_backtest(primary_df, tick_df, funding_rates=funding_rates, **common_kwargs)
        result["market_type"] = args.market_type

    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, allow_nan=False,
    ).encode("utf-8")
    manifest["manifest_hash"] = hashlib.sha256(manifest_bytes).hexdigest()
    result["manifest"] = manifest

    output = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"Đã ghi kết quả vào {args.out}", file=sys.stderr)
    print(output)

    if not args.no_mlflow and args.walk_forward <= 1:
        run_id = experiment.log_backtest_run(
            result,
            params={
                "symbol": args.symbol,
                "timeframe": args.timeframe,
                "tick_timeframe": args.tick_timeframe,
                "days": args.days,
                "buy_threshold": args.buy_threshold or config.BUY_SCORE_THRESHOLD,
                "watch_threshold": args.watch_threshold or config.WATCH_SCORE_THRESHOLD,
                "fee_pct": result["fee_pct"],
                "slippage_pct": result["slippage_pct"],
                "engine": "paper_engine",
                "scoring_profile": args.scoring_profile,
                "sr_required_swings": args.sr_required_swings,
            },
            run_name=f"paper_{args.symbol}_{args.timeframe}_{args.days}d",
        )
        print(f"MLflow run_id: {run_id}", file=sys.stderr)


if __name__ == "__main__":
    main()
