"""BUY LIMIT tại Fibonacci retracement sau bullish CHOCH."""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.analyze_choch_entry import (  # noqa: E402
    HORIZON_MINUTES, MIN_RR, MIN_TP_PCT, _load, _metrics, _path_arrays,
    _segments, _trade_outcome,
)
from src.engine import risk  # noqa: E402
from src.indicators import technical  # noqa: E402


FIB_LEVELS = (0.382, 0.500, 0.618)
FILL_WINDOW_MINUTES = 240


def _limit_events(source_events, primary, path_ts, path_price, cost_pct, fib):
    rows = []
    for source in source_events:
        bottom = primary.iloc[source["bottom_idx"]]
        signal = primary.iloc[source["signal_idx"]]
        impulse_high = float(signal["high"])
        impulse_low = float(bottom["low"])
        limit_price = impulse_high - fib * (impulse_high - impulse_low)
        stop_price = float(source["stop_price"])
        if limit_price <= stop_price:
            continue
        start_time = pd.Timestamp(signal["ts"]) + pd.Timedelta(minutes=5)
        end_time = start_time + pd.Timedelta(minutes=FILL_WINDOW_MINUTES)
        start = int(np.searchsorted(path_ts, np.datetime64(start_time), side="left"))
        end = int(np.searchsorted(path_ts, np.datetime64(end_time), side="right"))
        candidates = np.flatnonzero(path_price[start:end] <= limit_price)
        if not len(candidates):
            continue
        fill_pos = start + int(candidates[0])
        fill_time = pd.Timestamp(path_ts[fill_pos])
        risk_pct = (limit_price - stop_price) / limit_price * 100
        tp_pct = max(MIN_TP_PCT, MIN_RR * risk_pct)
        tp_price = limit_price * (1 + tp_pct / 100)
        outcome = _trade_outcome(path_ts, path_price, fill_time, limit_price, stop_price, tp_price, cost_pct)
        if outcome is None:
            continue
        reason, net_return = outcome
        rows.append({
            "variant": f"choch_fib_{fib}", "signal_idx": source["signal_idx"],
            "signal_ts": source["signal_ts"], "entry_ts": str(fill_time),
            "entry_price": limit_price, "stop_price": stop_price, "tp_price": tp_price,
            "risk_pct": risk_pct, "tp_pct": tp_pct,
            "exit_reason": reason, "net_return_pct": float(net_return),
            "features": {**source["features"], "fib_level": fib, "fill_delay_minutes": (fill_time - start_time).total_seconds() / 60},
        })
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-cache", required=True)
    parser.add_argument("--choch-artifact", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    raw = _load(Path(args.dataset_cache))
    primary = technical.add_indicators(technical.to_dataframe(raw["primary"]))
    tick = technical.to_dataframe(raw["tick"])
    path_ts, path_price = _path_arrays(tick)
    choch = json.loads(Path(args.choch_artifact).read_text(encoding="utf-8"))
    source = choch["events"]["choch_direct"]
    cost_pct = risk.round_trip_cost_pct()
    dataset_end = pd.Timestamp(primary["ts"].iloc[-1])
    validation_end = dataset_end - pd.Timedelta(days=30)
    train_end = validation_end - pd.Timedelta(days=30)
    events, summaries = {}, {}
    for fib in FIB_LEVELS:
        name = f"fib_{fib}"
        rows = _limit_events(source, primary, path_ts, path_price, cost_pct, fib)
        events[name] = rows
        segmented = _segments(rows, train_end, validation_end)
        summaries[name] = {"all": _metrics(rows), **{key: _metrics(value) for key, value in segmented.items()}}
    output = {
        "contract": {"source": "confirmed swing low -> CHOCH direct", "fib_levels": FIB_LEVELS, "fill_window_minutes": FILL_WINDOW_MINUTES, "sl": "swing low - 0.20 ATR", "tp": f"max({MIN_TP_PCT}%, {MIN_RR} x risk)", "cost_pct": cost_pct, "horizon_minutes": HORIZON_MINUTES, "train_end_exclusive": str(train_end), "validation_end_exclusive": str(validation_end)},
        "dataset": choch["dataset"], "summaries": summaries, "events": events,
    }
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summaries": summaries}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
