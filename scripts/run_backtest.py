"""CLI chạy Backtest Engine trên dữ liệu lịch sử thật.

Usage:
    python scripts/run_backtest.py [--symbol BTC/USDT] [--timeframe 5m] [--days 30]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, experiment  # noqa: E402
from src.data import market  # noqa: E402
from src.indicators import technical  # noqa: E402
from src.backtest import engine  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=config.SYMBOL)
    parser.add_argument("--timeframe", default=config.TIMEFRAME)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--out", default=None, help="Ghi kết quả JSON ra file, mặc định chỉ in ra stdout")
    parser.add_argument(
        "--buy-threshold", type=float, default=None,
        help="Ngưỡng BUY riêng cho backtest (xem giới hạn toán học trong engine.py) — mặc định dùng BUY_SCORE_THRESHOLD sống",
    )
    parser.add_argument("--watch-threshold", type=float, default=None)
    parser.add_argument("--no-mlflow", action="store_true", help="Bỏ qua log MLflow, chỉ in kết quả")
    args = parser.parse_args()

    exchange = market.get_exchange()
    print(f"Đang tải OHLCV {args.symbol} {args.timeframe} — {args.days} ngày gần nhất...", file=sys.stderr)
    raw_ohlcv = market.fetch_historical_ohlcv(exchange, args.symbol, args.timeframe, args.days)
    if not raw_ohlcv:
        print("Không lấy được dữ liệu OHLCV.", file=sys.stderr)
        sys.exit(1)

    df = technical.to_dataframe(raw_ohlcv)
    print(f"Có {len(df)} bar. Đang chạy backtest...", file=sys.stderr)
    result = engine.run_backtest(
        df, symbol=args.symbol, timeframe=args.timeframe,
        buy_threshold=args.buy_threshold, watch_threshold=args.watch_threshold,
    )

    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"Đã ghi kết quả vào {args.out}", file=sys.stderr)
    print(output)

    if not args.no_mlflow:
        run_id = experiment.log_backtest_run(
            result,
            params={
                "symbol": args.symbol,
                "timeframe": args.timeframe,
                "days": args.days,
                "buy_threshold": args.buy_threshold or config.BUY_SCORE_THRESHOLD,
                "watch_threshold": args.watch_threshold or config.WATCH_SCORE_THRESHOLD,
                "fee_pct": result["fee_pct"],
                "slippage_pct": result["slippage_pct"],
            },
            run_name=f"{args.symbol}_{args.timeframe}_{args.days}d",
        )
        print(f"MLflow run_id: {run_id}", file=sys.stderr)


if __name__ == "__main__":
    main()
