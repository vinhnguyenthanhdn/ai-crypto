"""CLI train Entry Model (LightGBM) trên dữ liệu lịch sử thật.

Usage:
    python scripts/train_entry_model.py [--symbol BTC/USDT] [--timeframe 5m] [--days 60]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, experiment  # noqa: E402
from src.data import market  # noqa: E402
from src.indicators import technical  # noqa: E402
from src.ml import entry_model  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=config.SYMBOL)
    parser.add_argument("--timeframe", default=config.TIMEFRAME)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--horizon-bars", type=int, default=entry_model.DEFAULT_HORIZON_BARS)
    parser.add_argument("--label-threshold-pct", type=float, default=entry_model.DEFAULT_LABEL_THRESHOLD_PCT)
    parser.add_argument("--out", default=None, help="Đường dẫn lưu model (joblib), mặc định không lưu file")
    parser.add_argument("--no-mlflow", action="store_true", help="Bỏ qua log MLflow/Model Registry")
    args = parser.parse_args()

    exchange = market.get_exchange()
    print(f"Đang tải OHLCV {args.symbol} {args.timeframe} — {args.days} ngày gần nhất...", file=sys.stderr)
    raw_ohlcv = market.fetch_historical_ohlcv(exchange, args.symbol, args.timeframe, args.days)
    if not raw_ohlcv:
        print("Không lấy được dữ liệu OHLCV.", file=sys.stderr)
        sys.exit(1)

    df = technical.to_dataframe(raw_ohlcv)
    print(f"Có {len(df)} bar. Đang build dataset (horizon={args.horizon_bars} bar, "
          f"threshold={args.label_threshold_pct}%)...", file=sys.stderr)
    dataset = entry_model.build_dataset(df, args.horizon_bars, args.label_threshold_pct)
    print(f"Dataset: {len(dataset)} dòng, positive rate {dataset['label'].mean():.3f}", file=sys.stderr)

    model, metrics = entry_model.train_entry_model(dataset)
    print("Metrics (test set, walk-forward):")
    for k, v in metrics.items():
        print(f"  {k}: {v}")

    if args.out:
        import joblib

        joblib.dump(model, args.out)
        print(f"Đã lưu model vào {args.out}", file=sys.stderr)

    if not args.no_mlflow:
        run_id = experiment.log_entry_model_run(
            model,
            metrics=metrics,
            params={
                "symbol": args.symbol,
                "timeframe": args.timeframe,
                "days": args.days,
                "horizon_bars": args.horizon_bars,
                "label_threshold_pct": args.label_threshold_pct,
                "feature_columns": ",".join(entry_model.FEATURE_COLUMNS),
            },
            run_name=f"{args.symbol}_{args.timeframe}_{args.days}d",
        )
        print(f"MLflow run_id: {run_id}", file=sys.stderr)


if __name__ == "__main__":
    main()
