"""Causal formation/trading split for crypto perpetual pairs trading."""
import argparse
import itertools
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from discover_cross_sectional_momentum import load_funding, load_panel, metrics


WINDOW_DAYS = (7, 14, 30)
ENTRY_Z = (1.5, 2.0)
EXIT_Z = (0.25, 0.5)
STOP_Z = (3.0, 4.0)
MAX_HOLD_DAYS = (7, 14)
BASE_COST = 0.07
STRESS_COST = 0.15


def fit_spread(y, x):
    beta = float(np.cov(y, x, ddof=1)[0, 1] / np.var(x, ddof=1))
    intercept = float(y.mean() - beta * x.mean())
    spread = y - intercept - beta * x
    lag, delta = spread[:-1], np.diff(spread)
    design = np.column_stack((np.ones(len(lag)), lag))
    coefficients = np.linalg.lstsq(design, delta, rcond=None)[0]
    residual = delta - design @ coefficients
    variance = float(residual @ residual / max(len(delta) - 2, 1))
    standard_error = float(np.sqrt(variance * np.linalg.inv(design.T @ design)[1, 1]))
    adf_t = float(coefficients[1] / standard_error) if standard_error else 0.0
    return intercept, beta, adf_t


def replay(open_price, funding, zscore, pair, beta, start, end, entry_z,
           exit_z, stop_z, max_hold_days, cost):
    index = open_price.index[(open_price.index >= start) & (open_price.index < end)]
    y, x = pair
    normalizer = 1 + abs(beta)
    spread_weights = np.array([1 / normalizer, -beta / normalizer])
    position = 0
    held = 0
    values, timestamps, trade_returns = [], [], []
    current_trade = 0.0
    entries = 0
    previous_prices = None
    prices_panel = open_price.loc[index, [y, x]].to_numpy(dtype=float)
    funding_panel = funding.loc[index, [y, x]].to_numpy(dtype=float)
    signal_index = index - pd.Timedelta(hours=1)
    signals = zscore.reindex(signal_index).to_numpy(dtype=float)
    for offset, timestamp in enumerate(index):
        prices = prices_panel[offset]
        gross = 0.0
        if previous_prices is not None and position:
            weights = position * spread_weights
            gross = float(weights @ (prices / previous_prices - 1))
            gross += float((-weights * funding_panel[offset]).sum())
            current_trade += gross
            held += 1
        signal = signals[offset]
        new_position = position
        if position:
            if (abs(signal) <= exit_z or abs(signal) >= stop_z
                    or held >= max_hold_days * 24):
                new_position = 0
        elif np.isfinite(signal) and abs(signal) >= entry_z and abs(signal) < stop_z:
            new_position = -1 if signal > 0 else 1
            entries += 1
            held = 0
            current_trade = 0.0
        turnover = abs(new_position - position)
        net = gross - turnover * cost / 100
        if position and not new_position:
            current_trade -= turnover * cost / 100
            trade_returns.append(current_trade)
        elif not position and new_position:
            current_trade -= turnover * cost / 100
        values.append(net)
        timestamps.append(timestamp)
        position = new_position
        previous_prices = prices
    if position:
        close_cost = cost / 100
        values[-1] -= close_cost
        current_trade -= close_cost
        trade_returns.append(current_trade)
    assets = Counter({("PAIR", f"{y}/{x}"): entries})
    return np.asarray(values), pd.DatetimeIndex(timestamps), assets, trade_returns


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    close, open_price, _ = load_panel(Path(args.data_dir))
    funding = load_funding(Path(args.data_dir), close.index, close.columns)
    log_close = np.log(close)
    start, end = close.index[0], close.index[-1] + pd.Timedelta(hours=1)
    formation_end = start + pd.DateOffset(months=6)
    train_end = start + pd.DateOffset(months=18)
    validation_end = train_end + pd.DateOffset(months=6)
    formation = log_close[(log_close.index >= start) & (log_close.index < formation_end)]
    pair_models = []
    for y, x in itertools.combinations(close.columns, 2):
        intercept, beta, adf_t = fit_spread(formation[y].to_numpy(), formation[x].to_numpy())
        if beta > 0 and adf_t < -2.86:
            pair_models.append({"pair": (y, x), "intercept": intercept, "beta": beta, "adf_t": adf_t})
    pair_models.sort(key=lambda item: item["adf_t"])
    pair_models = pair_models[:10]
    splits = {"train": (formation_end, train_end), "validation": (train_end, validation_end), "test": (validation_end, end)}
    grid = []
    for model in pair_models:
        y, x = model["pair"]
        spread = log_close[y] - model["intercept"] - model["beta"] * log_close[x]
        for window, entry_z, exit_z, stop_z, max_hold in itertools.product(
                WINDOW_DAYS, ENTRY_Z, EXIT_Z, STOP_Z, MAX_HOLD_DAYS):
            rolling = spread.rolling(window * 24, min_periods=window * 24)
            zscore = (spread - rolling.mean()) / rolling.std().replace(0, np.nan)
            values, timestamps, assets, trades = replay(
                open_price, funding, zscore, model["pair"], model["beta"],
                *splits["train"], entry_z, exit_z, stop_z, max_hold, BASE_COST,
            )
            result = metrics(values, timestamps, assets, *splits["train"], True)
            result["round_trips"] = len(trades)
            grid.append({"pair": list(model["pair"]), "intercept": model["intercept"],
                         "beta": model["beta"], "formation_adf_t": model["adf_t"],
                         "window_days": window, "entry_z": entry_z, "exit_z": exit_z,
                         "stop_z": stop_z, "max_hold_days": max_hold, "metrics": {"train": result}})
    eligible = [item for item in grid if item["metrics"]["train"]["net_return_pct"] > 0
                and (item["metrics"]["train"]["profit_factor"] or 0) > 1.1
                and item["metrics"]["train"]["max_drawdown_pct"] <= 15
                and item["metrics"]["train"]["round_trips"] >= 12]
    selected = max(eligible, key=lambda item: item["metrics"]["train"]["net_return_pct"] /
                   max(item["metrics"]["train"]["max_drawdown_pct"], .01)) if eligible else None
    stress = {}
    if selected:
        y, x = selected["pair"]
        spread = log_close[y] - selected["intercept"] - selected["beta"] * log_close[x]
        rolling = spread.rolling(selected["window_days"] * 24, min_periods=selected["window_days"] * 24)
        zscore = (spread - rolling.mean()) / rolling.std().replace(0, np.nan)
        params = [selected[k] for k in ("entry_z", "exit_z", "stop_z", "max_hold_days")]
        for name, bounds in splits.items():
            for cost, destination in ((BASE_COST, selected["metrics"]), (STRESS_COST, stress)):
                values, timestamps, assets, trades = replay(open_price, funding, zscore, (y, x), selected["beta"], *bounds, *params, cost)
                result = metrics(values, timestamps, assets, *bounds, name == "train")
                result["round_trips"] = len(trades)
                destination[name] = result
    passed = bool(selected and all(selected["metrics"][name]["net_return_pct"] > 0
                  and (selected["metrics"][name]["profit_factor"] or 0) > 1
                  for name in ("validation", "test")) and all(stress[name]["net_return_pct"] > 0
                  and (stress[name]["profit_factor"] or 0) > 1 for name in splits))
    output = {"passed": passed, "contract": {"formation": "first 6m only", "selection": "remaining 12m train only",
              "execution": "prior 1h close signal; next 1h open", "funding_included": True,
              "base_one_way_cost_pct": BASE_COST, "stress_one_way_cost_pct": STRESS_COST},
              "dataset": {"start": str(start), "formation_end": str(formation_end), "train_end": str(train_end),
              "validation_end": str(validation_end), "end": str(end), "symbols": list(close.columns)},
              "formation_pairs": [{**item, "pair": list(item["pair"])} for item in pair_models],
              "grid_size": len(grid), "eligible_count": len(eligible), "selected": selected,
              "cost_stress": stress, "grid": grid}
    Path(args.out).write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps({key: output[key] for key in ("passed", "formation_pairs", "grid_size", "eligible_count", "selected", "cost_stress")}, indent=2))


if __name__ == "__main__":
    main()
