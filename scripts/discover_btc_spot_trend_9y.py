"""Low-turnover BTC Spot long/cash trend benchmark over nine years."""
import argparse
import gzip
import itertools
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from discover_cross_sectional_momentum import metrics


FAST_DAYS = (1, 10, 20, 50)
SLOW_DAYS = (50, 100, 200, 300)
BUFFER_PCT = (0.0, 1.0, 2.0)
VOLATILITY_DAYS = (30, 60)
TARGET_VOLATILITY_PCT = (10.0, 20.0, 30.0)
BASE_ONE_WAY_COST_PCT = 0.12
STRESS_ONE_WAY_COST_PCT = 0.24


def load_daily(path):
    with gzip.open(path, "rt", encoding="utf-8") as source:
        frame = pd.DataFrame(json.load(source)["rows"])
    frame["ts"] = pd.to_datetime(frame.ts, unit="ms")
    frame = frame.set_index("ts").sort_index()
    daily = frame.resample("1D").agg({"open": "first", "close": "last"}).dropna()
    return daily


def replay(daily, signal, start, end, cost):
    index = daily.index[(daily.index >= start) & (daily.index < end)]
    opens = daily.open.reindex(index).to_numpy(dtype=float)
    desired = signal.shift(1).reindex(index).fillna(0.0).to_numpy(dtype=float)
    position = 0.0
    values, timestamps, entries = [], [], 0
    for offset, timestamp in enumerate(index):
        gross = float(position * (opens[offset] / opens[offset - 1] - 1)) if offset else 0.0
        new_position = float(desired[offset])
        turnover = abs(new_position - position)
        values.append(gross - turnover * cost / 100)
        if new_position > 0 and position == 0:
            entries += 1
        position = new_position
        timestamps.append(timestamp)
    if position and len(values):
        values[-1] -= cost / 100
    return np.asarray(values), pd.DatetimeIndex(timestamps), Counter({("LONG", "BTCUSDT_SPOT"): entries})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    daily = load_daily(Path(args.data))
    start, end = daily.index[0], daily.index[-1] + pd.Timedelta(days=1)
    train_end = start + pd.DateOffset(years=6)
    validation_end = train_end + pd.DateOffset(years=1)
    splits = {"train": (start, train_end), "validation": (train_end, validation_end), "test": (validation_end, end)}
    moving_averages = {days: daily.close.rolling(days, min_periods=days).mean()
                       for days in set(FAST_DAYS + SLOW_DAYS)}
    grid = []
    daily_volatility = {
        days: daily.close.pct_change(fill_method=None).rolling(days, min_periods=days).std() * np.sqrt(365)
        for days in VOLATILITY_DAYS
    }
    for fast, slow, buffer_pct, volatility_days, target_volatility_pct in itertools.product(
            FAST_DAYS, SLOW_DAYS, BUFFER_PCT, VOLATILITY_DAYS, TARGET_VOLATILITY_PCT):
        if fast >= slow:
            continue
        trend = moving_averages[fast] > moving_averages[slow] * (1 + buffer_pct / 100)
        exposure = (target_volatility_pct / 100 / daily_volatility[volatility_days]).clip(upper=1.0)
        signal = trend.astype(float) * exposure
        values, timestamps, assets = replay(daily, signal, *splits["train"], BASE_ONE_WAY_COST_PCT)
        result = metrics(values, timestamps, assets, *splits["train"], True)
        grid.append({"fast_days": fast, "slow_days": slow, "buffer_pct": buffer_pct,
                     "volatility_days": volatility_days, "target_volatility_pct": target_volatility_pct,
                     "metrics": {"train": result}})
    eligible = [item for item in grid if item["metrics"]["train"]["net_return_pct"] > 0
                and (item["metrics"]["train"]["profit_factor"] or 0) > 1.05
                and item["metrics"]["train"]["max_drawdown_pct"] <= 35
                and item["metrics"]["train"]["positive_quarters"] >=
                np.ceil(item["metrics"]["train"]["quarter_count"] * .55)]
    selected = max(eligible, key=lambda item: item["metrics"]["train"]["net_return_pct"] /
                   max(item["metrics"]["train"]["max_drawdown_pct"], .01)) if eligible else None
    stress = {}
    if selected:
        trend = moving_averages[selected["fast_days"]] > moving_averages[selected["slow_days"]] * (1 + selected["buffer_pct"] / 100)
        exposure = (selected["target_volatility_pct"] / 100 /
                    daily_volatility[selected["volatility_days"]]).clip(upper=1.0)
        signal = trend.astype(float) * exposure
        for name, bounds in splits.items():
            for cost, destination in ((BASE_ONE_WAY_COST_PCT, selected["metrics"]),
                                      (STRESS_ONE_WAY_COST_PCT, stress)):
                values, timestamps, assets = replay(daily, signal, *bounds, cost)
                destination[name] = metrics(values, timestamps, assets, *bounds, name == "train")
    passed = bool(selected and all(selected["metrics"][name]["net_return_pct"] > 0
                  and (selected["metrics"][name]["profit_factor"] or 0) > 1
                  for name in ("validation", "test")) and all(stress[name]["net_return_pct"] > 0
                  and (stress[name]["profit_factor"] or 0) > 1 for name in splits))
    output = {"passed": passed, "contract": {"market": "Binance BTCUSDT Spot", "direction": "long_cash",
              "selection": "first 6y train only", "execution": "prior daily close signal; next daily open",
              "sizing": "causal volatility target capped at 1x, no leverage",
              "base_one_way_cost_pct": BASE_ONE_WAY_COST_PCT,
              "stress_one_way_cost_pct": STRESS_ONE_WAY_COST_PCT},
              "dataset": {"start": str(start), "train_end": str(train_end),
              "validation_end": str(validation_end), "end": str(end), "days": len(daily)},
              "grid_size": len(grid), "eligible_count": len(eligible), "selected": selected,
              "cost_stress": stress, "grid": grid}
    Path(args.out).write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps({key: output[key] for key in ("passed", "dataset", "grid_size", "eligible_count", "selected", "cost_stress")}, indent=2))


if __name__ == "__main__":
    main()
