"""Forward-ordered cross-sectional momentum discovery over 2021-2026."""
import argparse
import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

from discover_cross_sectional_momentum import load_funding, load_panel, metrics, replay


LOOKBACK_DAYS = (7, 14, 30, 60)
REBALANCE_DAYS = (1, 3, 7, 14)
BUCKET_SIZE = (1, 2, 3)
LIQUID_SIZE = (8, 12, 15)
SCORE_MODE = ("raw", "vol_adjusted")
BASE_COST = 0.07
STRESS_COST = 0.15


def combine(old_dir, new_dir):
    old = load_panel(old_dir)
    new = load_panel(new_dir)
    panels = []
    for old_panel, new_panel in zip(old, new):
        panel = pd.concat((old_panel, new_panel)).sort_index()
        panel = panel[~panel.index.duplicated(keep="last")]
        panels.append(panel)
    common = panels[0].index
    for panel in panels[1:]:
        common = common.intersection(panel.index)
    return tuple(panel.reindex(common) for panel in panels)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-dir", required=True)
    parser.add_argument("--new-dir", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    old_dir, new_dir = Path(args.old_dir), Path(args.new_dir)
    close, open_price, dollar_volume = combine(old_dir, new_dir)
    funding = (load_funding(old_dir, close.index, close.columns)
               + load_funding(new_dir, close.index, close.columns))
    hourly_returns = close.pct_change(fill_method=None)
    liquidity = dollar_volume.rolling(30 * 24, min_periods=30 * 24).mean()
    scores = {}
    for lookback in LOOKBACK_DAYS:
        bars = lookback * 24
        momentum = close / close.shift(bars) - 1
        volatility = hourly_returns.rolling(bars, min_periods=bars).std().replace(0, np.nan)
        scores[(lookback, "raw")] = momentum
        scores[(lookback, "vol_adjusted")] = momentum / volatility
    start, end = close.index[0], close.index[-1] + pd.Timedelta(hours=1)
    train_end = pd.Timestamp("2023-08-07")
    validation_end = pd.Timestamp("2024-08-07")
    splits = {"train": (start, train_end), "validation": (train_end, validation_end),
              "test": (validation_end, end)}
    grid = []
    for lookback, rebalance, bucket, liquid_size, mode in itertools.product(
            LOOKBACK_DAYS, REBALANCE_DAYS, BUCKET_SIZE, LIQUID_SIZE, SCORE_MODE):
        values, timestamps, assets = replay(
            close, open_price, funding, scores[(lookback, mode)], liquidity,
            *splits["train"], rebalance, bucket, liquid_size, "long_short", BASE_COST,
        )
        result = metrics(values, timestamps, assets, *splits["train"], True)
        grid.append({"lookback_days": lookback, "rebalance_days": rebalance,
                     "bucket_size": bucket, "liquid_universe_size": liquid_size,
                     "score_mode": mode, "portfolio_mode": "long_short",
                     "metrics": {"train": result}})
    eligible = [item for item in grid if item["metrics"]["train"]["net_return_pct"] > 0
                and (item["metrics"]["train"]["profit_factor"] or 0) > 1.1
                and item["metrics"]["train"]["max_drawdown_pct"] <= 25
                and item["metrics"]["train"]["positive_quarters"] >=
                np.ceil(item["metrics"]["train"]["quarter_count"] * .6)]
    selected = max(eligible, key=lambda item: item["metrics"]["train"]["net_return_pct"] /
                   max(item["metrics"]["train"]["max_drawdown_pct"], .01)) if eligible else None
    stress = {}
    if selected:
        params = [selected[key] for key in ("lookback_days", "rebalance_days", "bucket_size",
                                             "liquid_universe_size", "score_mode", "portfolio_mode")]
        for name, bounds in splits.items():
            for cost, destination in ((BASE_COST, selected["metrics"]), (STRESS_COST, stress)):
                values, timestamps, assets = replay(close, open_price, funding, scores[(params[0], params[4])],
                                                     liquidity, *bounds, *params[1:4], params[5], cost)
                destination[name] = metrics(values, timestamps, assets, *bounds, name == "train")
    passed = bool(selected and all(selected["metrics"][name]["net_return_pct"] > 0
                  and (selected["metrics"][name]["profit_factor"] or 0) > 1
                  for name in ("validation", "test")) and all(stress[name]["net_return_pct"] > 0
                  and (stress[name]["profit_factor"] or 0) > 1 for name in splits))
    output = {"passed": passed, "contract": {"selection": "2021-2023 train only",
              "execution": "prior 1h close signal; next scheduled 1h open", "funding_included": True,
              "base_one_way_cost_pct": BASE_COST, "stress_one_way_cost_pct": STRESS_COST},
              "dataset": {"start": str(start), "train_end": str(train_end),
              "validation_end": str(validation_end), "end": str(end), "rows": len(close),
              "symbols": list(close.columns)}, "grid_size": len(grid), "eligible_count": len(eligible),
              "selected": selected, "cost_stress": stress, "grid": grid}
    Path(args.out).write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps({key: output[key] for key in ("passed", "dataset", "grid_size", "eligible_count", "selected", "cost_stress")}, indent=2))


if __name__ == "__main__":
    main()
