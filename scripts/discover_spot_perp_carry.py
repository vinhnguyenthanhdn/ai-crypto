"""Train-only discovery for unlevered, delta-neutral spot/perpetual carry."""
import argparse
import gzip
import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

from discover_cross_sectional_momentum import load_funding, metrics


LOOKBACK_DAYS = (7, 14, 30)
REBALANCE_DAYS = (1, 3, 7)
BUCKET_SIZE = (1, 3, 5)
MIN_ANNUALIZED_FUNDING_PCT = (5, 10, 20)
BASE_ONE_WAY_COST_PCT = 0.10
STRESS_ONE_WAY_COST_PCT = 0.20


def load_prices(spot_dir, perp_dir):
    def panel(directory, pattern):
        opens, closes = {}, {}
        for path in sorted(directory.glob(pattern)):
            symbol = path.name.split("_", 1)[0].upper()
            with gzip.open(path, "rt", encoding="utf-8") as source:
                frame = pd.DataFrame(json.load(source)["rows"])
            frame["ts"] = pd.to_datetime(frame.ts, unit="ms")
            frame = frame.set_index("ts").sort_index()
            opens[symbol], closes[symbol] = frame.open, frame.close
        return pd.DataFrame(opens), pd.DataFrame(closes)

    spot_open, spot_close = panel(spot_dir, "*_spot_1h_*.json.gz")
    perp_open, perp_close = panel(perp_dir, "*_1h_*.json.gz")
    symbols = spot_open.columns.intersection(perp_open.columns)
    index = spot_open.index.intersection(perp_open.index)
    return tuple(frame.reindex(index=index, columns=symbols) for frame in (
        spot_open, spot_close, perp_open, perp_close,
    ))


def weights_at(signal, timestamp, bucket, minimum):
    location = signal.index.get_indexer([timestamp], method="pad")[0]
    if location < 0:
        return pd.Series(0.0, index=signal.columns)
    row = signal.iloc[location].dropna()
    row = row[row >= minimum].nlargest(bucket)
    weights = pd.Series(0.0, index=signal.columns)
    if len(row):
        weights.loc[row.index] = 1 / len(row)
    return weights


def replay(spot_open, perp_open, funding, signal, start, end, rebalance_days,
           bucket, minimum, one_way_cost_pct):
    schedule = pd.date_range(start.ceil("D"), end.floor("D"), freq=f"{rebalance_days}D")
    schedule = schedule[schedule.isin(spot_open.index)]
    prior_weights = pd.Series(0.0, index=spot_open.columns)
    prior_spot = prior_perp = prior_ts = None
    values, timestamps = [], []
    allocations = {}
    for timestamp in schedule:
        new_weights = weights_at(signal, timestamp - pd.Timedelta(hours=1), bucket, minimum)
        gross = 0.0
        if prior_ts is not None:
            spot_return = spot_open.loc[timestamp] / prior_spot - 1
            perp_return = perp_open.loc[timestamp] / prior_perp - 1
            funding_sum = funding[(funding.index > prior_ts) & (funding.index <= timestamp)].sum()
            gross = float((0.5 * prior_weights * (spot_return - perp_return + funding_sum)).sum())
        turnover = float((new_weights - prior_weights).abs().sum())
        values.append(gross - turnover * one_way_cost_pct / 100)
        timestamps.append(timestamp)
        for symbol in new_weights[new_weights > 0].index:
            allocations[("CARRY", symbol)] = allocations.get(("CARRY", symbol), 0) + 1
        prior_weights = new_weights
        prior_spot, prior_perp, prior_ts = spot_open.loc[timestamp], perp_open.loc[timestamp], timestamp
    if prior_ts is not None:
        final_ts = spot_open.index[spot_open.index < end][-1]
        spot_return = spot_open.loc[final_ts] / prior_spot - 1
        perp_return = perp_open.loc[final_ts] / prior_perp - 1
        funding_sum = funding[(funding.index > prior_ts) & (funding.index <= final_ts)].sum()
        gross = float((0.5 * prior_weights * (spot_return - perp_return + funding_sum)).sum())
        values.append(gross - float(prior_weights.abs().sum()) * one_way_cost_pct / 100)
        timestamps.append(final_ts)
    from collections import Counter
    return np.asarray(values), pd.DatetimeIndex(timestamps), Counter(allocations)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--spot-dir", required=True)
    parser.add_argument("--perp-dir", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    spot_open, spot_close, perp_open, perp_close = load_prices(Path(args.spot_dir), Path(args.perp_dir))
    funding = load_funding(Path(args.perp_dir), spot_open.index, spot_open.columns)
    start, end = spot_open.index[0], spot_open.index[-1] + pd.Timedelta(hours=1)
    train_end = start + pd.DateOffset(months=18)
    validation_end = train_end + pd.DateOffset(months=6)
    splits = {"train": (start, train_end), "validation": (train_end, validation_end), "test": (validation_end, end)}
    signals = {}
    for days in LOOKBACK_DAYS:
        # Settlements are sparse; rolling sum is observed carry, annualized causally.
        signals[days] = funding.rolling(days * 24, min_periods=days * 24).sum() * 365 / days * 100
    grid = []
    for lookback, rebalance, bucket, minimum in itertools.product(
            LOOKBACK_DAYS, REBALANCE_DAYS, BUCKET_SIZE, MIN_ANNUALIZED_FUNDING_PCT):
        values, timestamps, assets = replay(
            spot_open, perp_open, funding, signals[lookback], *splits["train"],
            rebalance, bucket, minimum, BASE_ONE_WAY_COST_PCT,
        )
        result = metrics(values, timestamps, assets, *splits["train"], True)
        grid.append({"lookback_days": lookback, "rebalance_days": rebalance,
                     "bucket_size": bucket, "minimum_annualized_funding_pct": minimum,
                     "metrics": {"train": result}})
    eligible = [item for item in grid if item["metrics"]["train"]["net_return_pct"] > 0
                and (item["metrics"]["train"]["profit_factor"] or 0) > 1.1
                and item["metrics"]["train"]["max_drawdown_pct"] <= 10]
    selected = max(eligible, key=lambda item: item["metrics"]["train"]["net_return_pct"] /
                   max(item["metrics"]["train"]["max_drawdown_pct"], 0.01)) if eligible else None
    stress = {}
    if selected:
        params = [selected[k] for k in ("lookback_days", "rebalance_days", "bucket_size", "minimum_annualized_funding_pct")]
        for name, bounds in splits.items():
            values, timestamps, assets = replay(spot_open, perp_open, funding, signals[params[0]], *bounds, *params[1:], BASE_ONE_WAY_COST_PCT)
            selected["metrics"][name] = metrics(values, timestamps, assets, *bounds, name == "train")
            values, timestamps, assets = replay(spot_open, perp_open, funding, signals[params[0]], *bounds, *params[1:], STRESS_ONE_WAY_COST_PCT)
            stress[name] = metrics(values, timestamps, assets, *bounds)
    passed = bool(selected and all(selected["metrics"][x]["net_return_pct"] > 0 and
                  (selected["metrics"][x]["profit_factor"] or 0) > 1 for x in ("validation", "test"))
                  and all(stress[x]["net_return_pct"] > 0 and
                  (stress[x]["profit_factor"] or 0) > 1 for x in splits))
    output = {"passed": passed, "contract": {"capital": "50% spot + 50% 1x-margined short perpetual",
              "execution": "prior 1h signal, next scheduled 1h open", "funding_included": True,
              "base_one_way_cost_pct": BASE_ONE_WAY_COST_PCT, "stress_one_way_cost_pct": STRESS_ONE_WAY_COST_PCT},
              "dataset": {"start": str(start), "train_end": str(train_end), "validation_end": str(validation_end),
              "end": str(end), "rows": len(spot_open), "symbols": list(spot_open.columns)},
              "grid_size": len(grid), "eligible_count": len(eligible), "selected": selected,
              "cost_stress": stress, "grid": grid}
    Path(args.out).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: output[k] for k in ("passed", "dataset", "eligible_count", "selected", "cost_stress")}, indent=2))


if __name__ == "__main__":
    main()
