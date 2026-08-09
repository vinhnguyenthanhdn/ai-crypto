"""Walk-forward Donchian/Turtle entry trên 4h được tổng hợp từ nến 5m."""
import argparse
import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd


CHANNELS = ((12, 6), (30, 15), (60, 30), (120, 60))
STOP_ATR = (2.0, 3.0, 4.0, 6.0, None)
ROUND_TRIP_COST_PCT = 0.30


def _load_4h(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        raw = json.load(fh)
    frame = pd.DataFrame(raw["rows"])
    frame["ts"] = pd.to_datetime(frame.ts, unit="ms")
    frame = frame.set_index("ts")
    bars = frame.resample("4h", origin="epoch", label="left", closed="left").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"), count=("close", "count"),
    )
    bars = bars[bars["count"] == 48].drop(columns="count").dropna().copy()
    prior_close = bars.close.shift(1)
    true_range = pd.concat([
        bars.high - bars.low, (bars.high - prior_close).abs(), (bars.low - prior_close).abs(),
    ], axis=1).max(axis=1)
    bars["atr"] = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    return bars


def _metrics(trades):
    if not trades:
        return {"n": 0, "mean_net_return_pct": None, "profit_factor": None, "win_rate_pct": None, "sum_net_return_pct": None}
    returns = np.asarray([trade["net_return_pct"] for trade in trades])
    gains, losses = returns[returns > 0].sum(), abs(returns[returns < 0].sum())
    equity = np.cumsum(returns)
    drawdown = equity - np.maximum.accumulate(np.r_[0.0, equity])[-len(equity):]
    return {
        "n": len(trades), "win_rate_pct": round(float((returns > 0).mean() * 100), 4),
        "mean_net_return_pct": round(float(returns.mean()), 6),
        "sum_net_return_pct": round(float(returns.sum()), 6),
        "profit_factor": round(float(gains / losses), 6) if losses else None,
        "max_additive_drawdown_pct": round(float(drawdown.min()), 6),
        "long_trades": sum(t["side"] == "LONG" for t in trades),
        "short_trades": sum(t["side"] == "SHORT" for t in trades),
        "stop_exits": sum(t["exit_reason"] == "INITIAL_STOP" for t in trades),
    }


def _run_segment(bars, start, end, entry_window, exit_window, stop_atr):
    segment = bars[(bars.index >= start) & (bars.index < end)].copy()
    if len(segment) < entry_window + 2:
        return []
    segment["entry_high"] = segment.high.shift(1).rolling(entry_window).max()
    segment["entry_low"] = segment.low.shift(1).rolling(entry_window).min()
    segment["exit_high"] = segment.high.shift(1).rolling(exit_window).max()
    segment["exit_low"] = segment.low.shift(1).rolling(exit_window).min()
    rows = list(segment.itertuples())
    trades, position, pending = [], None, None
    for i, row in enumerate(rows):
        if pending is not None:
            side, reason = pending
            price = float(row.open)
            if reason == "ENTRY":
                stop = None
                if stop_atr is not None:
                    stop = price - stop_atr * float(rows[i - 1].atr) if side == "LONG" else price + stop_atr * float(rows[i - 1].atr)
                position = {"side": side, "entry_ts": row.Index, "entry_price": price, "stop": stop}
            elif position is not None:
                gross = (price / position["entry_price"] - 1) * 100 if position["side"] == "LONG" else (position["entry_price"] - price) / position["entry_price"] * 100
                trades.append({**position, "exit_ts": row.Index, "exit_price": price, "exit_reason": reason, "net_return_pct": gross - ROUND_TRIP_COST_PCT})
                position = None
            pending = None

        if position is not None and position["stop"] is not None:
            hit = row.low <= position["stop"] if position["side"] == "LONG" else row.high >= position["stop"]
            if hit:
                price = float(position["stop"])
                gross = (price / position["entry_price"] - 1) * 100 if position["side"] == "LONG" else (position["entry_price"] - price) / position["entry_price"] * 100
                trades.append({**position, "exit_ts": row.Index, "exit_price": price, "exit_reason": "INITIAL_STOP", "net_return_pct": gross - ROUND_TRIP_COST_PCT})
                position = None

        if i + 1 >= len(rows) or pd.isna(row.entry_high) or pd.isna(row.atr):
            continue
        if position is None:
            if row.close > row.entry_high:
                pending = ("LONG", "ENTRY")
            elif row.close < row.entry_low:
                pending = ("SHORT", "ENTRY")
        elif position["side"] == "LONG" and row.close < row.exit_low:
            pending = ("LONG", "CHANNEL_EXIT")
        elif position["side"] == "SHORT" and row.close > row.exit_high:
            pending = ("SHORT", "CHANNEL_EXIT")

    if position is not None:
        row = rows[-1]; price = float(row.close)
        gross = (price / position["entry_price"] - 1) * 100 if position["side"] == "LONG" else (position["entry_price"] - price) / position["entry_price"] * 100
        trades.append({**position, "exit_ts": row.Index, "exit_price": price, "exit_reason": "SEGMENT_END", "net_return_pct": gross - ROUND_TRIP_COST_PCT})
    return trades


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--flow-cache", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    bars = _load_4h(Path(args.flow_cache))
    start, end = bars.index[0], bars.index[-1]
    train_end = start + pd.Timedelta(days=365)
    validation_end = train_end + (end - train_end) / 2
    splits = {"train": (start, train_end), "validation": (train_end, validation_end), "test": (validation_end, end + pd.Timedelta(hours=4))}
    grid = []
    for entry_window, exit_window in CHANNELS:
        for stop_atr in STOP_ATR:
            metrics = {name: _metrics(_run_segment(bars, *bounds, entry_window, exit_window, stop_atr)) for name, bounds in splits.items()}
            grid.append({"entry_window_4h": entry_window, "exit_window_4h": exit_window, "initial_stop_atr": stop_atr, "metrics": metrics})
    eligible = [item for item in grid if item["metrics"]["train"]["n"] >= 10]
    selected = max(eligible, key=lambda item: (item["metrics"]["train"]["mean_net_return_pct"], item["metrics"]["train"]["profit_factor"] or 0))
    values = [selected["metrics"][name] for name in ("train", "validation", "test")]
    passes = [selected] if all(value["n"] >= 8 and value["mean_net_return_pct"] > 0 and (value["profit_factor"] is None or value["profit_factor"] > 1) for value in values) else []
    output = {
        "contract": {"source": "Binance BTCUSDT spot 5m aggregated to closed 4h", "entry_fill": "next 4h open", "exit": "opposite Donchian channel or fixed initial ATR stop", "round_trip_cost_pct": ROUND_TRIP_COST_PCT, "selection": "parameters selected on first 365d only", "minimum_train_trades": 10, "minimum_holdout_trades": 8},
        "dataset": {"bars_4h": len(bars), "start": str(start), "train_end": str(train_end), "validation_end": str(validation_end), "end": str(end)},
        "passes": passes, "selected": selected, "grid": grid,
    }
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passes": passes, "selected": selected}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
