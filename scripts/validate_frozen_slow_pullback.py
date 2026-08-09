"""Validate frozen 4h trend-pullback contract trên dữ liệu hoàn toàn mới."""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.analyze_slow_donchian_entry import _load_4h, _metrics  # noqa: E402
from scripts.analyze_slow_mean_reversion_entry import _run_segment  # noqa: E402


LOOKBACK_4H = 30
ENTRY_Z = 1.5
DEFAULT_STOP_ATR = 5.0
DEFAULT_DIRECTION_MODE = "PRICE_EMA180"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--flow-cache", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--stop-atr", type=float, default=DEFAULT_STOP_ATR)
    parser.add_argument("--entry-z", type=float, default=ENTRY_Z)
    parser.add_argument("--lookback-4h", type=int, default=LOOKBACK_4H)
    parser.add_argument("--direction-mode", choices=("PRICE_EMA180", "PRICE_EMA180_SLOPE", "EMA60_180"), default=DEFAULT_DIRECTION_MODE)
    args = parser.parse_args()
    bars = _load_4h(Path(args.flow_cache))
    bars["ema60"] = bars.close.ewm(span=60, adjust=False).mean()
    bars["ema180"] = bars.close.ewm(span=180, adjust=False).mean()
    bars["ema180_slope"] = bars.ema180.pct_change(30)
    start, end = bars.index[0], bars.index[-1]
    train_end = start + pd.Timedelta(days=365)
    validation_end = train_end + (end - train_end) / 2
    splits = {"train": (start, train_end), "validation": (train_end, validation_end), "test": (validation_end, end + pd.Timedelta(hours=4))}
    metrics = {
        name: _metrics(_run_segment(bars, *bounds, args.lookback_4h, args.entry_z, args.stop_atr, args.direction_mode))
        for name, bounds in splits.items()
    }
    values = [metrics[name] for name in ("train", "validation", "test")]
    passed = all(value["n"] >= 8 and value["mean_net_return_pct"] > 0 and (value["profit_factor"] is None or value["profit_factor"] > 1) for value in values)
    output = {
        "contract": {"frozen_before_dataset_open": True, "timeframe": "4h_from_5m", "lookback_4h": args.lookback_4h, "entry_z": args.entry_z, "direction_mode": args.direction_mode, "initial_stop_atr": args.stop_atr, "exit": "z returns to zero", "fill": "next 4h open", "round_trip_cost_pct": 0.30, "minimum_trades_each_segment": 8},
        "dataset": {"symbol": args.symbol, "bars_4h": len(bars), "start": str(start), "train_end": str(train_end), "validation_end": str(validation_end), "end": str(end)},
        "passed": passed, "metrics": metrics,
    }
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
