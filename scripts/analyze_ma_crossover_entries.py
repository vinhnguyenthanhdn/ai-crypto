"""Walk-forward 5m EMA crossover: entry và opposite-cross exit causal."""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.analyze_choch_entry import _load  # noqa: E402
from src.engine import risk  # noqa: E402
from src.indicators import technical  # noqa: E402


PAIRS = ((12, 72), (12, 144), (36, 144), (36, 288), (72, 288), (72, 576), (144, 576), (288, 1152))


def _trade_segment(df, fast, slow, side, start, end, cost):
    fast_ema = df.close.ewm(span=fast, adjust=False).mean()
    slow_ema = df.close.ewm(span=slow, adjust=False).mean()
    cross_up = (fast_ema > slow_ema) & (fast_ema.shift(1) <= slow_ema.shift(1))
    cross_down = (fast_ema < slow_ema) & (fast_ema.shift(1) >= slow_ema.shift(1))
    entries = cross_up if side == "LONG" else cross_down
    exits = cross_down if side == "LONG" else cross_up
    trades, position = [], None
    for i in range(slow + 1, len(df) - 1):
        signal_ts = pd.Timestamp(df.ts.iloc[i])
        fill_idx = i + 1
        fill_ts = pd.Timestamp(df.ts.iloc[fill_idx])
        if position is None:
            if start <= fill_ts < end and bool(entries.iloc[i]):
                position = (fill_idx, float(df.open.iloc[fill_idx]))
        elif bool(exits.iloc[i]) or fill_ts >= end:
            entry_idx, entry = position
            exit_price = float(df.open.iloc[fill_idx])
            gross = (exit_price / entry - 1) * 100 if side == "LONG" else (entry - exit_price) / entry * 100
            trades.append({"entry_ts": str(df.ts.iloc[entry_idx]), "exit_ts": str(fill_ts), "net_return_pct": gross - cost})
            position = None
        if signal_ts >= end:
            break
    return trades


def _metrics(trades):
    if not trades:
        return {"n": 0, "mean_net_return_pct": None, "profit_factor": None, "win_rate_pct": None}
    values = np.asarray([x["net_return_pct"] for x in trades])
    gain, loss = values[values > 0].sum(), abs(values[values < 0].sum())
    return {"n": len(trades), "win_rate_pct": round(float((values > 0).mean() * 100), 4), "mean_net_return_pct": round(float(values.mean()), 6), "sum_net_return_pct": round(float(values.sum()), 6), "profit_factor": round(float(gain / loss), 6) if loss else None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-cache", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    raw = _load(Path(args.dataset_cache))
    df = technical.to_dataframe(raw["primary"])
    cost = risk.round_trip_cost_pct()
    start, end = pd.Timestamp(df.ts.iloc[0]), pd.Timestamp(df.ts.iloc[-1])
    validation_start, test_start = end - pd.Timedelta(days=60), end - pd.Timedelta(days=30)
    splits = {"train": (start, validation_start), "validation": (validation_start, test_start), "test": (test_start, end)}
    results = {}
    for fast, slow in PAIRS:
        for side in ("LONG", "SHORT"):
            name = f"{side.lower()}_ema_{fast}_{slow}"
            results[name] = {segment: _metrics(_trade_segment(df, fast, slow, side, *bounds, cost)) for segment, bounds in splits.items()}
    passes = []
    for name, metrics in results.items():
        values = [metrics[key] for key in ("train", "validation", "test")]
        if all(x["n"] >= 20 and x["mean_net_return_pct"] is not None and x["mean_net_return_pct"] > 0 and (x["profit_factor"] is None or x["profit_factor"] > 1) for x in values):
            passes.append({"entry": name, "metrics": metrics})
    output = {"contract": {"entry_timeframe": "5m", "entry": "EMA crossover at close, fill next open", "exit": "opposite EMA crossover, fill next open", "round_trip_cost_pct": cost, "promotion_minimum_each_segment": 20}, "dataset": {"start": str(start), "validation_start": str(validation_start), "test_start": str(test_start), "end": str(end)}, "passes": passes, "results": results}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passes": passes, "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
