"""Tìm pullback entry 1h/2h/4h đạt đồng thời performance và frequency gate."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


COST_PCT = 0.30
TIMEFRAME_GRIDS = {
    "1h": {"z_lookbacks": (24, 48, 72), "trend_spans": (168, 360), "stop_atr": (8.0, 12.0)},
    "2h": {"z_lookbacks": (12, 24, 36), "trend_spans": (84, 180), "stop_atr": (5.0, 8.0)},
    "4h": {"z_lookbacks": (6, 12, 18), "trend_spans": (42, 90), "stop_atr": (3.0, 5.0)},
}
ENTRY_Z_VALUES = (1.0, 1.25, 1.5)
LIMIT_OFFSETS_PCT = (0.0, 0.10, 0.20, 0.30)
EXIT_Z_VALUES = (0.0, 0.25, 0.50)


def _load_bars(path, timeframe):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        raw = json.load(fh)
    frame = pd.DataFrame(raw["rows"])
    frame["ts"] = pd.to_datetime(frame.ts, unit="ms")
    frame = frame.set_index("ts")
    expected = int(pd.Timedelta(timeframe) / pd.Timedelta(minutes=5))
    bars = frame.resample(timeframe, origin="epoch", label="left", closed="left").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"), count=("close", "count"),
    )
    bars = bars[bars["count"] == expected].drop(columns="count").dropna().copy()
    prior_close = bars.close.shift(1)
    true_range = pd.concat([bars.high - bars.low, (bars.high - prior_close).abs(), (bars.low - prior_close).abs()], axis=1).max(axis=1)
    bars["atr"] = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    return bars


def _run(bars, start, end, z_lookback, trend_span, entry_z, exit_z, stop_atr, limit_offset_pct):
    mean = bars.close.rolling(z_lookback).mean()
    std = bars.close.rolling(z_lookback).std().replace(0, np.nan)
    zscore = (bars.close - mean) / std
    trend = bars.close.ewm(span=trend_span, adjust=False).mean()
    frame = bars[(bars.index >= start) & (bars.index < end)].copy()
    frame["z"] = zscore.reindex(frame.index)
    frame["trend"] = trend.reindex(frame.index)
    rows, trades, position, pending = list(frame.itertuples()), [], None, None
    for i, row in enumerate(rows):
        if pending:
            action, side, requested_price = pending
            price = float(row.open) if requested_price is None else float(requested_price)
            if action == "ENTRY":
                filled = requested_price is None or (row.low <= requested_price if side == "LONG" else row.high >= requested_price)
                if not filled:
                    pending = None
                    # Order hết hạn sau một bar; bar hiện tại vẫn được dùng tạo signal mới ở close.
                    price = None
                else:
                    atr = float(rows[i - 1].atr)
                    stop = price - stop_atr * atr if side == "LONG" else price + stop_atr * atr
                    position = {"side": side, "entry_ts": row.Index, "entry_price": price, "stop": stop}
            elif position is not None:
                gross = (price / position["entry_price"] - 1) * 100 if side == "LONG" else (position["entry_price"] - price) / position["entry_price"] * 100
                trades.append({**position, "exit_ts": row.Index, "exit_price": price, "exit_reason": "MEAN_EXIT", "net_return_pct": gross - COST_PCT})
                position = None
            pending = None
        if position is not None:
            hit = row.low <= position["stop"] if position["side"] == "LONG" else row.high >= position["stop"]
            if hit:
                price = float(position["stop"]); side = position["side"]
                gross = (price / position["entry_price"] - 1) * 100 if side == "LONG" else (position["entry_price"] - price) / position["entry_price"] * 100
                trades.append({**position, "exit_ts": row.Index, "exit_price": price, "exit_reason": "INITIAL_STOP", "net_return_pct": gross - COST_PCT})
                position = None
        if i + 1 >= len(rows) or pd.isna(row.z) or pd.isna(row.atr):
            continue
        if position is None:
            if row.close >= row.trend and row.z <= -entry_z:
                requested = None if limit_offset_pct == 0 else float(row.close * (1 - limit_offset_pct / 100))
                pending = ("ENTRY", "LONG", requested)
            elif row.close < row.trend and row.z >= entry_z:
                requested = None if limit_offset_pct == 0 else float(row.close * (1 + limit_offset_pct / 100))
                pending = ("ENTRY", "SHORT", requested)
        elif position["side"] == "LONG" and row.z >= exit_z:
            pending = ("EXIT", "LONG", None)
        elif position["side"] == "SHORT" and row.z <= -exit_z:
            pending = ("EXIT", "SHORT", None)
    if position is not None:
        row = rows[-1]; price = float(row.close); side = position["side"]
        gross = (price / position["entry_price"] - 1) * 100 if side == "LONG" else (position["entry_price"] - price) / position["entry_price"] * 100
        trades.append({**position, "exit_ts": row.Index, "exit_price": price, "exit_reason": "SEGMENT_END", "net_return_pct": gross - COST_PCT})
    return trades


def _metrics(trades, start, end):
    if not trades:
        return {"n": 0, "mean_net_return_pct": None, "profit_factor": None, "average_per_30d": 0, "rolling_median_30d": 0, "pct_windows_10_20": 0}
    values = np.asarray([trade["net_return_pct"] for trade in trades])
    gains, losses = values[values > 0].sum(), abs(values[values < 0].sum())
    entries = pd.DatetimeIndex([pd.Timestamp(trade["entry_ts"]) for trade in trades])
    checkpoints = pd.date_range(start.normalize() + pd.Timedelta(days=30), end.normalize(), freq="1D")
    counts = np.asarray([sum((entries > point - pd.Timedelta(days=30)) & (entries <= point)) for point in checkpoints])
    return {
        "n": len(trades), "win_rate_pct": round(float((values > 0).mean() * 100), 4),
        "mean_net_return_pct": round(float(values.mean()), 6), "sum_net_return_pct": round(float(values.sum()), 6),
        "profit_factor": round(float(gains / losses), 6) if losses else None,
        "average_per_30d": round(float(len(trades) / ((end - start) / pd.Timedelta(days=30))), 6),
        "rolling_p10_30d": float(np.quantile(counts, 0.10)), "rolling_median_30d": float(np.median(counts)),
        "rolling_p90_30d": float(np.quantile(counts, 0.90)),
        "pct_windows_10_20": round(float(((counts >= 10) & (counts <= 20)).mean() * 100), 4),
        "pct_zero_windows": round(float((counts == 0).mean() * 100), 4),
    }


def _performance_pass(metric):
    return metric["mean_net_return_pct"] is not None and metric["mean_net_return_pct"] > 0 and (metric["profit_factor"] is None or metric["profit_factor"] > 1)


def _frequency_pass(metric):
    return 10 <= metric["average_per_30d"] <= 20 and 10 <= metric["rolling_median_30d"] <= 20 and metric["pct_windows_10_20"] >= 50


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--flow-cache", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    source = Path(args.flow_cache)
    all_bars = {timeframe: _load_bars(source, timeframe) for timeframe in TIMEFRAME_GRIDS}
    reference = all_bars["1h"]
    start, end = reference.index[0], reference.index[-1] + pd.Timedelta(hours=1)
    train_end, validation_end = start + pd.Timedelta(days=1460), start + pd.Timedelta(days=1825)
    splits = {"train": (start, train_end), "validation": (train_end, validation_end), "test": (validation_end, end)}
    grid = []
    for timeframe, values in TIMEFRAME_GRIDS.items():
        bars = all_bars[timeframe]
        for z_lookback in values["z_lookbacks"]:
            for trend_span in values["trend_spans"]:
                for stop_atr in values["stop_atr"]:
                    for entry_z in ENTRY_Z_VALUES:
                        for exit_z in EXIT_Z_VALUES:
                            for limit_offset_pct in LIMIT_OFFSETS_PCT:
                                metrics = {name: _metrics(_run(bars, *bounds, z_lookback, trend_span, entry_z, exit_z, stop_atr, limit_offset_pct), *bounds) for name, bounds in splits.items()}
                                grid.append({"timeframe": timeframe, "z_lookback_bars": z_lookback, "trend_ema_bars": trend_span, "entry_z": entry_z, "exit_z": exit_z, "stop_atr": stop_atr, "limit_offset_pct": limit_offset_pct, "metrics": metrics})
    train_eligible = [item for item in grid if _performance_pass(item["metrics"]["train"]) and _frequency_pass(item["metrics"]["train"])]
    selected = max(train_eligible, key=lambda item: (item["metrics"]["train"]["mean_net_return_pct"], item["metrics"]["train"]["profit_factor"] or 0)) if train_eligible else None
    passed = bool(selected and all(_performance_pass(selected["metrics"][name]) and _frequency_pass(selected["metrics"][name]) for name in splits))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    output = {"contract": {"selection": "train only", "performance_gate": "mean net > 0 and PF > 1 in every segment", "frequency_gate": "average and rolling median 10-20 per 30d; >=50% rolling windows within 10-20", "round_trip_cost_pct": COST_PCT, "single_concurrent_position": True}, "dataset": {"source": str(source), "sha256": digest, "start": str(start), "train_end": str(train_end), "validation_end": str(validation_end), "end": str(end)}, "passed": passed, "selected": selected, "train_eligible_count": len(train_eligible), "grid": grid}
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": passed, "train_eligible_count": len(train_eligible), "selected": selected}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
