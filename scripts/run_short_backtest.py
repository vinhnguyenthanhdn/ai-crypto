"""CLI chạy Backtest Engine cho chiến lược Short riêng (thử nghiệm — phát hiện
"buy đỉnh cục bộ" từ AI Review Backtest).

Usage:
    python scripts/run_short_backtest.py [--symbol BTC/USDT] [--timeframe 5m] [--days 30]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402
from src.data import market  # noqa: E402
from src.indicators import technical  # noqa: E402
from src.backtest import short_engine  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=config.SYMBOL)
    parser.add_argument("--timeframe", default=config.TIMEFRAME)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--out", default=None)
    parser.add_argument("--short-threshold", type=float, default=None)
    parser.add_argument("--watch-threshold", type=float, default=None)
    args = parser.parse_args()

    exchange = market.get_exchange()
    print(f"Đang tải OHLCV {args.symbol} {args.timeframe} — {args.days} ngày gần nhất...", file=sys.stderr)
    raw_ohlcv = market.fetch_historical_ohlcv(exchange, args.symbol, args.timeframe, args.days)
    if not raw_ohlcv:
        print("Không lấy được dữ liệu OHLCV.", file=sys.stderr)
        sys.exit(1)

    df = technical.to_dataframe(raw_ohlcv)
    print(f"Có {len(df)} bar. Đang chạy backtest Short...", file=sys.stderr)
    result = short_engine.run_backtest_short(
        df, symbol=args.symbol, timeframe=args.timeframe,
        short_threshold=args.short_threshold, watch_threshold=args.watch_threshold,
    )

    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"Đã ghi kết quả vào {args.out}", file=sys.stderr)
    print(output)


if __name__ == "__main__":
    main()
