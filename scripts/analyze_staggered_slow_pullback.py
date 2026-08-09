"""Kiểm định chia nhỏ entry của slow trend-pullback thành nhiều tranche."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


COST_PCT = 0.30
LOOKBACKS = (30, 60)
ENTRY_Z_VALUES = (0.75, 1.00, 1.25, 1.50, 1.75, 2.00)
EXIT_Z_VALUES = (0.00, 0.25)
STOP_ATR_VALUES = (1.0, 2.0, 3.0, 5.0, 8.0)
MAX_TRANCHES_VALUES = (3, 5, 8, 10)
LIMIT_OFFSETS_PCT = (0.0, 0.20)


def _load_bars(path: Path) -> pd.DataFrame:
    with gzip.open(path, "rt", encoding="utf-8") as source:
        raw = json.load(source)
    frame = pd.DataFrame(raw["rows"])
    frame["ts"] = pd.to_datetime(frame.ts, unit="ms")
    frame = frame.set_index("ts")
    bars = frame.resample("4h", origin="epoch", label="left", closed="left").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), count=("close", "count"),
    )
    bars = bars[bars["count"] == 48].drop(columns="count").dropna().copy()
    prior_close = bars.close.shift(1)
    true_range = pd.concat(
        [bars.high - bars.low, (bars.high - prior_close).abs(), (bars.low - prior_close).abs()],
        axis=1,
    ).max(axis=1)
    bars["atr"] = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    bars["ema180"] = bars.close.ewm(span=180, adjust=False).mean()
    return bars


def _run(bars, start, end, lookback, entry_z, exit_z, stop_atr, max_tranches, limit_offset_pct):
    mean = bars.close.rolling(lookback).mean()
    std = bars.close.rolling(lookback).std().replace(0, np.nan)
    frame = bars[(bars.index >= start) & (bars.index < end)].copy()
    frame["z"] = ((bars.close - mean) / std).reindex(frame.index)
    rows = list(frame.itertuples())
    trades, positions, pending_entry, pending_exit = [], [], None, False
    active_side, entries_in_excursion, excursion_id = None, 0, 0

    def close(position, timestamp, price, reason):
        gross = ((price / position["entry_price"] - 1) * 100 if position["side"] == "LONG"
                 else (position["entry_price"] - price) / position["entry_price"] * 100)
        trades.append({**position, "exit_ts": timestamp, "exit_price": price,
                       "exit_reason": reason, "net_return_pct": gross - COST_PCT})

    for i, row in enumerate(rows):
        if pending_exit:
            for position in positions:
                close(position, row.Index, float(row.open), "MEAN_EXIT")
            positions = []
            pending_exit = False
            active_side, entries_in_excursion = None, 0

        if pending_entry is not None:
            side, requested = pending_entry
            filled = requested is None or (row.low <= requested if side == "LONG" else row.high >= requested)
            if filled:
                price = float(row.open) if requested is None else float(requested)
                atr = float(rows[i - 1].atr)
                stop = price - stop_atr * atr if side == "LONG" else price + stop_atr * atr
                positions.append({"side": side, "entry_ts": row.Index, "entry_price": price,
                                  "stop": stop, "tranche_capital_fraction": 1 / max_tranches,
                                  "excursion_id": excursion_id})
                entries_in_excursion += 1
            pending_entry = None

        survivors = []
        for position in positions:
            hit = row.low <= position["stop"] if position["side"] == "LONG" else row.high >= position["stop"]
            if hit:
                close(position, row.Index, float(position["stop"]), "INITIAL_STOP")
            else:
                survivors.append(position)
        positions = survivors

        if i + 1 >= len(rows) or pd.isna(row.z) or pd.isna(row.atr):
            continue
        exit_crossed = active_side is not None and (
            (active_side == "LONG" and row.z >= exit_z)
            or (active_side == "SHORT" and row.z <= -exit_z)
        )
        if exit_crossed:
            if positions:
                pending_exit = True
            else:
                active_side, entries_in_excursion = None, 0
            continue
        signal_side = None
        if row.close >= row.ema180 and row.z <= -entry_z:
            signal_side = "LONG"
        elif row.close < row.ema180 and row.z >= entry_z:
            signal_side = "SHORT"
        if active_side is None and signal_side is not None:
            excursion_id += 1
            active_side = signal_side
        if (signal_side is not None and signal_side == active_side
                and entries_in_excursion < max_tranches and pending_entry is None):
            requested = None
            if limit_offset_pct:
                multiplier = 1 - limit_offset_pct / 100 if signal_side == "LONG" else 1 + limit_offset_pct / 100
                requested = float(row.close * multiplier)
            pending_entry = (signal_side, requested)

    for position in positions:
        close(position, rows[-1].Index, float(rows[-1].close), "SEGMENT_END")
    return trades


def _metrics(trades, start, end):
    if not trades:
        return {"n": 0, "mean_net_return_pct": None, "profit_factor": None,
                "average_per_30d": 0, "rolling_median_30d": 0, "pct_windows_10_20": 0}
    values = np.asarray([trade["net_return_pct"] for trade in trades])
    gains, losses = values[values > 0].sum(), abs(values[values < 0].sum())
    entries = pd.DatetimeIndex([trade["entry_ts"] for trade in trades])
    checkpoints = pd.date_range(start.normalize() + pd.Timedelta(days=30), end.normalize(), freq="1D")
    counts = np.asarray([sum((entries > point - pd.Timedelta(days=30)) & (entries <= point)) for point in checkpoints])
    return {
        "n": len(trades), "win_rate_pct": round(float((values > 0).mean() * 100), 4),
        "mean_net_return_pct": round(float(values.mean()), 6),
        "sum_ticket_net_return_pct": round(float(values.sum()), 6),
        "profit_factor": round(float(gains / losses), 6) if losses else None,
        "average_per_30d": round(float(len(trades) / ((end - start) / pd.Timedelta(days=30))), 6),
        "rolling_p10_30d": float(np.quantile(counts, .10)),
        "rolling_median_30d": float(np.median(counts)),
        "rolling_p90_30d": float(np.quantile(counts, .90)),
        "pct_windows_10_20": round(float(((counts >= 10) & (counts <= 20)).mean() * 100), 4),
        "pct_zero_windows": round(float((counts == 0).mean() * 100), 4),
    }


def _performance(metric):
    return metric["mean_net_return_pct"] is not None and metric["mean_net_return_pct"] > 0 and metric["profit_factor"] > 1


def _frequency(metric):
    return (10 <= metric["average_per_30d"] <= 20 and 10 <= metric["rolling_median_30d"] <= 20
            and metric["pct_windows_10_20"] >= 50)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--flow-cache", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--train-days", type=int, default=1460)
    parser.add_argument("--validation-days", type=int, default=365)
    args = parser.parse_args()
    source = Path(args.flow_cache)
    bars = _load_bars(source)
    start, end = bars.index[0], bars.index[-1] + pd.Timedelta(hours=4)
    train_end = start + pd.Timedelta(days=args.train_days)
    validation_end = train_end + pd.Timedelta(days=args.validation_days)
    splits = {"train": (start, train_end), "validation": (train_end, validation_end), "test": (validation_end, end)}
    grid = []
    for lookback in LOOKBACKS:
        for entry_z in ENTRY_Z_VALUES:
            for exit_z in EXIT_Z_VALUES:
                for stop_atr in STOP_ATR_VALUES:
                    for max_tranches in MAX_TRANCHES_VALUES:
                        for limit_offset_pct in LIMIT_OFFSETS_PCT:
                            metrics = {name: _metrics(_run(bars, *bounds, lookback, entry_z, exit_z, stop_atr,
                                                          max_tranches, limit_offset_pct), *bounds)
                                       for name, bounds in splits.items()}
                            grid.append({"lookback_4h": lookback, "entry_z": entry_z, "exit_z": exit_z,
                                         "stop_atr": stop_atr, "max_tranches": max_tranches,
                                         "limit_offset_pct": limit_offset_pct, "metrics": metrics})
    eligible = [item for item in grid if _performance(item["metrics"]["train"]) and _frequency(item["metrics"]["train"])]
    # Frequency và performance là hard gate. Để tránh chọn stop quá rộng chỉ vì
    # nhiễu nhỏ ở mean, tạo plateau gồm candidate đạt >=95% mean train tốt nhất,
    # rồi chọn stop nhỏ nhất; các tie còn lại mới dùng coverage/PF/mean.
    selected = None
    if eligible:
        best_mean = max(item["metrics"]["train"]["mean_net_return_pct"] for item in eligible)
        plateau = [item for item in eligible if item["metrics"]["train"]["mean_net_return_pct"] >= 0.95 * best_mean]
        selected = max(
            plateau,
            key=lambda item: (
                -item["stop_atr"],
                item["metrics"]["train"]["pct_windows_10_20"],
                item["metrics"]["train"]["profit_factor"],
                item["metrics"]["train"]["mean_net_return_pct"],
            ),
        )
    passed = bool(selected and all(_performance(selected["metrics"][name]) and _frequency(selected["metrics"][name])
                                   for name in splits))
    output = {
        "contract": {"selection": "train only; hard gates, >=95% best train mean plateau, then smallest ATR stop",
                     "ticket_semantics": "correlated scale-in tranches, not independent setups",
                     "risk": "each ticket receives 1/max_tranches of the setup capital allocation",
                     "tranche_cap": "maximum fills per complete z-score excursion; reset only after exit-z cross",
                     "round_trip_cost_pct_per_ticket": COST_PCT,
                     "performance_gate": "mean net > 0 and PF > 1 in every segment",
                     "frequency_gate": "average and rolling median 10-20 tickets/30d; >=50% windows within range"},
        "dataset": {"source": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "start": str(start), "train_end": str(train_end), "validation_end": str(validation_end), "end": str(end)},
        "passed": passed, "train_eligible_count": len(eligible), "selected": selected, "grid": grid,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": passed, "train_eligible_count": len(eligible), "selected": selected}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
