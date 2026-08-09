"""Multi-asset time-series trend discovery with causal volatility scaling."""
import argparse
import itertools
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from discover_cross_sectional_momentum import load_funding, load_panel, metrics


LOOKBACK_DAYS = (14, 30, 60, 90)
REBALANCE_DAYS = (1, 3, 7)
LIQUID_UNIVERSE_SIZE = (8, 12, 15)
MODE = ("long_short", "long_flat")
BASE_COST = 0.07
STRESS_COST = 0.15


def target_weights(momentum, volatility, liquidity, timestamp, liquid_size, mode):
    index = momentum.index.get_indexer([timestamp], method="pad")[0]
    result = pd.Series(0.0, index=momentum.columns)
    if index < 0:
        return result
    universe = liquidity.iloc[index].nlargest(liquid_size).index
    trend = np.sign(momentum.iloc[index].reindex(universe)).replace(0, np.nan)
    if mode == "long_flat":
        trend = trend.where(trend > 0)
    raw = trend / volatility.iloc[index].reindex(universe).replace(0, np.nan)
    raw = raw.dropna()
    if len(raw):
        result.loc[raw.index] = raw / raw.abs().sum()
    return result


def replay(close, open_price, funding, momentum, volatility, liquidity, start, end,
           rebalance, liquid_size, mode, cost):
    schedule = pd.date_range(start.ceil("D"), end.floor("D"), freq=f"{rebalance}D")
    schedule = schedule[schedule.isin(open_price.index)]
    prior = pd.Series(0.0, index=close.columns)
    prior_open = prior_ts = None
    values, timestamps, assets = [], [], Counter()
    for timestamp in schedule:
        new = target_weights(momentum, volatility, liquidity, timestamp - pd.Timedelta(hours=1), liquid_size, mode)
        gross = 0.0
        if prior_ts is not None:
            gross = float((prior * (open_price.loc[timestamp] / prior_open - 1)).sum())
            paid = funding[(funding.index > prior_ts) & (funding.index <= timestamp)].sum()
            gross += float((-prior * paid).sum())
        turnover = float((new - prior).abs().sum())
        values.append(gross - turnover * cost / 100)
        timestamps.append(timestamp)
        for symbol, weight in new[new != 0].items():
            assets[("LONG" if weight > 0 else "SHORT", symbol)] += 1
        prior, prior_open, prior_ts = new, open_price.loc[timestamp], timestamp
    final_ts = close.index[close.index < end][-1]
    if prior_ts is not None:
        gross = float((prior * (close.loc[final_ts] / prior_open - 1)).sum())
        gross += float((-prior * funding[(funding.index > prior_ts) & (funding.index <= final_ts)].sum()).sum())
        values.append(gross - float(prior.abs().sum()) * cost / 100)
        timestamps.append(final_ts)
    return np.asarray(values), pd.DatetimeIndex(timestamps), assets


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    close, open_price, volume = load_panel(Path(args.data_dir))
    funding = load_funding(Path(args.data_dir), close.index, close.columns)
    returns = close.pct_change(fill_method=None)
    liquidity = volume.rolling(30 * 24, min_periods=30 * 24).mean()
    panels = {}
    for days in LOOKBACK_DAYS:
        bars = days * 24
        panels[days] = (close / close.shift(bars) - 1,
                        returns.rolling(bars, min_periods=bars).std().replace(0, np.nan))
    start, end = close.index[0], close.index[-1] + pd.Timedelta(hours=1)
    train_end, validation_end = start + pd.DateOffset(months=18), start + pd.DateOffset(months=24)
    splits = {"train": (start, train_end), "validation": (train_end, validation_end), "test": (validation_end, end)}
    grid = []
    for lookback, rebalance, liquid_size, mode in itertools.product(LOOKBACK_DAYS, REBALANCE_DAYS, LIQUID_UNIVERSE_SIZE, MODE):
        values, timestamps, assets = replay(close, open_price, funding, *panels[lookback], liquidity,
                                             *splits["train"], rebalance, liquid_size, mode, BASE_COST)
        result = metrics(values, timestamps, assets, *splits["train"], True)
        grid.append({"lookback_days": lookback, "rebalance_days": rebalance,
                     "liquid_universe_size": liquid_size, "mode": mode, "metrics": {"train": result}})
    eligible = [x for x in grid if x["metrics"]["train"]["net_return_pct"] > 0
                and (x["metrics"]["train"]["profit_factor"] or 0) > 1.1
                and x["metrics"]["train"]["max_drawdown_pct"] <= 20]
    selected = max(eligible, key=lambda x: x["metrics"]["train"]["net_return_pct"] /
                   max(x["metrics"]["train"]["max_drawdown_pct"], .01)) if eligible else None
    stress = {}
    if selected:
        params = [selected[k] for k in ("lookback_days", "rebalance_days", "liquid_universe_size", "mode")]
        for name, bounds in splits.items():
            for cost, destination in ((BASE_COST, selected["metrics"]), (STRESS_COST, stress)):
                values, timestamps, assets = replay(close, open_price, funding, *panels[params[0]], liquidity,
                                                     *bounds, *params[1:], cost)
                destination[name] = metrics(values, timestamps, assets, *bounds, name == "train")
    passed = bool(selected and all(selected["metrics"][x]["net_return_pct"] > 0 and
                  (selected["metrics"][x]["profit_factor"] or 0) > 1 for x in ("validation", "test"))
                  and all(stress[x]["net_return_pct"] > 0 and (stress[x]["profit_factor"] or 0) > 1 for x in splits))
    output = {"passed": passed, "contract": {"selection": "train only", "funding_included": True,
              "execution": "prior 1h signal, next scheduled 1h open", "base_one_way_cost_pct": BASE_COST,
              "stress_one_way_cost_pct": STRESS_COST}, "dataset": {"start": str(start), "train_end": str(train_end),
              "validation_end": str(validation_end), "end": str(end), "rows": len(close), "symbols": list(close.columns)},
              "grid_size": len(grid), "eligible_count": len(eligible), "selected": selected, "cost_stress": stress, "grid": grid}
    Path(args.out).write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps({k: output[k] for k in ("passed", "eligible_count", "selected", "cost_stress")}, indent=2))


if __name__ == "__main__":
    main()
