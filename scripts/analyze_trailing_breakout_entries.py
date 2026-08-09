"""Walk-forward Donchian entry với ATR chandelier exit, không cap winner bằng TP."""
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


WINDOWS = (12, 36, 72, 288)
ATR_MULTIPLIERS = (4.0, 8.0, 12.0, 20.0)
HOLD_BARS = (288, 864, 2016, 4032)


def _signals(df):
    up = (df.ema20 > df.ema50) & (df.ema50 > df.ema200)
    down = (df.ema20 < df.ema50) & (df.ema50 < df.ema200)
    output = {}
    for window in WINDOWS:
        high = df.high.shift(1).rolling(window).max()
        low = df.low.shift(1).rolling(window).min()
        long = df.close > high + 0.05 * df.atr
        short = df.close < low - 0.05 * df.atr
        output[f"long_donchian_{window}"] = long & ~long.shift(1).fillna(False)
        output[f"long_donchian_{window}_trend"] = long & up & ~(long & up).shift(1).fillna(False)
        output[f"short_donchian_{window}"] = short & ~short.shift(1).fillna(False)
        output[f"short_donchian_{window}_trend"] = short & down & ~(short & down).shift(1).fillna(False)
    return output


def _trade(df, entry_idx, side, multiplier, max_bars, cost):
    if entry_idx >= len(df):
        return None
    entry = float(df.open.iloc[entry_idx])
    atr = float(df.atr.iloc[entry_idx - 1])
    extreme = entry
    stop = entry - multiplier * atr if side == "LONG" else entry + multiplier * atr
    end = min(entry_idx + max_bars, len(df) - 1)
    for i in range(entry_idx, end + 1):
        row = df.iloc[i]
        if side == "LONG":
            if float(row.low) <= stop:
                gross = (stop / entry - 1) * 100
                return i, {"entry_ts": str(df.ts.iloc[entry_idx]), "exit_ts": str(row.ts), "exit_reason": "TRAILING_STOP", "net_return_pct": gross - cost}
            extreme = max(extreme, float(row.high))
            stop = max(stop, extreme - multiplier * float(row.atr))
        else:
            if float(row.high) >= stop:
                gross = (entry - stop) / entry * 100
                return i, {"entry_ts": str(df.ts.iloc[entry_idx]), "exit_ts": str(row.ts), "exit_reason": "TRAILING_STOP", "net_return_pct": gross - cost}
            extreme = min(extreme, float(row.low))
            stop = min(stop, extreme + multiplier * float(row.atr))
    exit_price = float(df.close.iloc[end])
    gross = (exit_price / entry - 1) * 100 if side == "LONG" else (entry - exit_price) / entry * 100
    return end, {"entry_ts": str(df.ts.iloc[entry_idx]), "exit_ts": str(df.ts.iloc[end]), "exit_reason": "TIMEOUT", "net_return_pct": gross - cost}


def _metrics(trades):
    if not trades:
        return {"n": 0, "mean_net_return_pct": None, "profit_factor": None, "win_rate_pct": None}
    values = np.asarray([x["net_return_pct"] for x in trades])
    gain, loss = values[values > 0].sum(), abs(values[values < 0].sum())
    return {"n": len(trades), "win_rate_pct": round(float((values > 0).mean() * 100), 4), "mean_net_return_pct": round(float(values.mean()), 6), "sum_net_return_pct": round(float(values.sum()), 6), "profit_factor": round(float(gain / loss), 6) if loss else None, "timeouts": sum(x["exit_reason"] == "TIMEOUT" for x in trades)}


def _segment(df, indices, side, start, end, multiplier, max_bars, cost):
    trades, available_idx = [], -1
    purge = pd.Timedelta(minutes=5 * max_bars)
    for signal_idx in indices:
        entry_idx = signal_idx + 1
        ts = pd.Timestamp(df.ts.iloc[entry_idx])
        if ts < start or ts >= end - purge or entry_idx <= available_idx:
            continue
        outcome = _trade(df, entry_idx, side, multiplier, max_bars, cost)
        if outcome:
            available_idx, trade = outcome
            trades.append(trade)
    return trades


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-cache", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    raw = _load(Path(args.dataset_cache))
    df = technical.add_indicators(technical.to_dataframe(raw["primary"]))
    cost = risk.round_trip_cost_pct()
    start, end = pd.Timestamp(df.ts.iloc[0]), pd.Timestamp(df.ts.iloc[-1])
    validation_start, test_start = end - pd.Timedelta(days=60), end - pd.Timedelta(days=30)
    splits = {"train": (start, validation_start), "validation": (validation_start, test_start), "test": (test_start, end)}
    selected, results = {}, {}
    for name, mask in _signals(df).items():
        side = "SHORT" if name.startswith("short_") else "LONG"
        indices = np.flatnonzero(mask.fillna(False).to_numpy())
        grid = []
        for multiplier in ATR_MULTIPLIERS:
            for max_bars in HOLD_BARS:
                metrics = {}
                for split, bounds in splits.items():
                    metrics[split] = _metrics(_segment(df, indices, side, *bounds, multiplier, max_bars, cost))
                grid.append({"atr_multiplier": multiplier, "max_hold_minutes": max_bars * 5, "metrics": metrics})
        eligible = [x for x in grid if x["metrics"]["train"]["n"] >= 30]
        best = max(eligible, key=lambda x: (x["metrics"]["train"]["mean_net_return_pct"], x["metrics"]["train"]["profit_factor"] or 0)) if eligible else None
        results[name] = {"raw_events": len(indices), "grid": grid, "train_selected": best}
        if best:
            selected[name] = best
    passes = []
    for name, item in selected.items():
        values = [item["metrics"][key] for key in ("train", "validation", "test")]
        if all(x["n"] >= 20 and x["mean_net_return_pct"] is not None and x["mean_net_return_pct"] > 0 and (x["profit_factor"] is None or x["profit_factor"] > 1) for x in values):
            passes.append({"entry": name, **item})
    output = {"contract": {"selection": "ATR multiplier and max hold selected on train only", "entry_timeframe": "5m", "exit": "causal ATR chandelier, updated after each bar", "round_trip_cost_pct": cost, "minimum_train_trades": 30, "promotion_minimum_each_segment": 20}, "dataset": {"start": str(start), "validation_start": str(validation_start), "test_start": str(test_start), "end": str(end)}, "passes": passes, "selected": selected, "results": results}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passes": passes, "selected": selected}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
