"""Walk-forward mean-reversion entry trên 4h, thoát khi giá quay về rolling mean."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.analyze_slow_donchian_entry import _load_4h, _metrics


LOOKBACKS = (30, 60, 120)
ENTRY_Z = (1.5, 2.0, 2.5)
STOP_ATR = (3.0, 5.0, 8.0, None)
COST_PCT = 0.30


def _run_segment(bars, start, end, lookback, entry_z, stop_atr, direction_mode):
    full_mean = bars.close.rolling(lookback).mean()
    full_std = bars.close.rolling(lookback).std().replace(0, np.nan)
    full_z = (bars.close - full_mean) / full_std
    frame = bars[(bars.index >= start) & (bars.index < end)].copy()
    frame["z"] = full_z.reindex(frame.index)
    rows, trades, position, pending = list(frame.itertuples()), [], None, None
    for i, row in enumerate(rows):
        if pending:
            action, side = pending
            price = float(row.open)
            if action == "ENTRY":
                atr = float(rows[i - 1].atr)
                stop = None if stop_atr is None else (price - stop_atr * atr if side == "LONG" else price + stop_atr * atr)
                position = {"side": side, "entry_ts": row.Index, "entry_price": price, "stop": stop}
            elif position is not None:
                gross = (price / position["entry_price"] - 1) * 100 if side == "LONG" else (position["entry_price"] - price) / position["entry_price"] * 100
                trades.append({**position, "exit_ts": row.Index, "exit_price": price, "exit_reason": "MEAN_EXIT", "net_return_pct": gross - COST_PCT})
                position = None
            pending = None

        if position is not None and position["stop"] is not None:
            hit = row.low <= position["stop"] if position["side"] == "LONG" else row.high >= position["stop"]
            if hit:
                price = float(position["stop"])
                gross = (price / position["entry_price"] - 1) * 100 if position["side"] == "LONG" else (position["entry_price"] - price) / position["entry_price"] * 100
                trades.append({**position, "exit_ts": row.Index, "exit_price": price, "exit_reason": "INITIAL_STOP", "net_return_pct": gross - COST_PCT})
                position = None

        if i + 1 >= len(rows) or pd.isna(row.z):
            continue
        if position is None:
            if direction_mode == "BOTH":
                long_allowed = short_allowed = True
            elif direction_mode == "PRICE_EMA180":
                long_allowed, short_allowed = row.close >= row.ema180, row.close < row.ema180
            elif direction_mode == "PRICE_EMA180_SLOPE":
                long_allowed = row.close >= row.ema180 and row.ema180_slope > 0
                short_allowed = row.close < row.ema180 and row.ema180_slope < 0
            else:
                long_allowed, short_allowed = row.ema60 >= row.ema180, row.ema60 < row.ema180
            if long_allowed and row.z <= -entry_z:
                pending = ("ENTRY", "LONG")
            elif short_allowed and row.z >= entry_z:
                pending = ("ENTRY", "SHORT")
        elif position["side"] == "LONG" and row.z >= 0:
            pending = ("EXIT", "LONG")
        elif position["side"] == "SHORT" and row.z <= 0:
            pending = ("EXIT", "SHORT")

    if position is not None:
        row = rows[-1]; price = float(row.close); side = position["side"]
        gross = (price / position["entry_price"] - 1) * 100 if side == "LONG" else (position["entry_price"] - price) / position["entry_price"] * 100
        trades.append({**position, "exit_ts": row.Index, "exit_price": price, "exit_reason": "SEGMENT_END", "net_return_pct": gross - COST_PCT})
    return trades


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--flow-cache", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--train-days", type=int, default=365)
    parser.add_argument("--validation-days", type=int)
    args = parser.parse_args()
    source_path = Path(args.flow_cache)
    bars = _load_4h(source_path)
    bars["ema60"] = bars.close.ewm(span=60, adjust=False).mean()
    bars["ema180"] = bars.close.ewm(span=180, adjust=False).mean()
    bars["ema180_slope"] = bars.ema180.pct_change(30)
    start, end = bars.index[0], bars.index[-1]
    train_end = start + pd.Timedelta(days=args.train_days)
    validation_end = train_end + pd.Timedelta(days=args.validation_days) if args.validation_days else train_end + (end - train_end) / 2
    splits = {"train": (start, train_end), "validation": (train_end, validation_end), "test": (validation_end, end + pd.Timedelta(hours=4))}
    grid = []
    for lookback in LOOKBACKS:
        for entry_z in ENTRY_Z:
            for stop_atr in STOP_ATR:
                for direction_mode in ("BOTH", "PRICE_EMA180", "PRICE_EMA180_SLOPE", "EMA60_180"):
                    metrics = {name: _metrics(_run_segment(bars, *bounds, lookback, entry_z, stop_atr, direction_mode)) for name, bounds in splits.items()}
                    grid.append({"lookback_4h": lookback, "entry_z": entry_z, "initial_stop_atr": stop_atr, "direction_mode": direction_mode, "metrics": metrics})
    minimum_train = max(15, int(args.train_days / 365 * 15))
    eligible = [item for item in grid if item["metrics"]["train"]["n"] >= minimum_train]
    selected = max(eligible, key=lambda item: (item["metrics"]["train"]["mean_net_return_pct"], item["metrics"]["train"]["profit_factor"] or 0))
    values = [selected["metrics"][name] for name in ("train", "validation", "test")]
    passes = [selected] if all(value["n"] >= 8 and value["mean_net_return_pct"] > 0 and (value["profit_factor"] is None or value["profit_factor"] > 1) for value in values) else []
    digest = hashlib.sha256()
    with source_path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    output = {"contract": {"source": "Binance BTCUSDT spot 5m aggregated to closed 4h", "entry": "rolling z-score extreme, fill next 4h open", "exit": "z-score returns to zero or initial ATR stop", "round_trip_cost_pct": COST_PCT, "selection": f"parameters selected on first {args.train_days}d only", "minimum_train_trades": minimum_train, "engine": "analyze_slow_mean_reversion_entry.py:v1"}, "dataset": {"flow_cache": str(source_path), "flow_cache_sha256": digest.hexdigest(), "bars_4h": len(bars), "start": str(start), "train_end": str(train_end), "validation_end": str(validation_end), "end": str(end)}, "passes": passes, "selected": selected, "grid": grid}
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passes": passes, "selected": selected}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
