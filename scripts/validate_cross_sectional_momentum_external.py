"""Validate the frozen cross-sectional momentum contract on an external block."""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from discover_cross_sectional_momentum import load_funding, load_panel, metrics, replay


CONTRACT = {
    "lookback_days": 14,
    "rebalance_days": 1,
    "bucket_size": 3,
    "liquid_universe_size": 8,
    "score_mode": "vol_adjusted_momentum",
    "portfolio_mode": "long_short",
}
BASE_ONE_WAY_COST_PCT = 0.07
STRESS_ONE_WAY_COST_PCT = 0.15


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    close, open_price, dollar_volume = load_panel(Path(args.data_dir))
    funding = load_funding(Path(args.data_dir), close.index, close.columns)
    bars = CONTRACT["lookback_days"] * 24
    hourly_returns = close.pct_change(fill_method=None)
    momentum = close / close.shift(bars) - 1
    volatility = hourly_returns.rolling(bars, min_periods=bars).std().replace(0, np.nan)
    score = momentum / volatility
    liquidity = dollar_volume.rolling(30 * 24, min_periods=30 * 24).mean()
    start, end = close.index[0], close.index[-1] + pd.Timedelta(hours=1)

    results = {}
    for label, cost in (("base", BASE_ONE_WAY_COST_PCT), ("stress", STRESS_ONE_WAY_COST_PCT)):
        values, timestamps, assets = replay(
            close, open_price, funding, score, liquidity, start, end,
            CONTRACT["rebalance_days"], CONTRACT["bucket_size"],
            CONTRACT["liquid_universe_size"], CONTRACT["portfolio_mode"], cost,
        )
        results[label] = metrics(values, timestamps, assets, start, end, True)

    passed = all(
        result["net_return_pct"] > 0
        and (result["profit_factor"] or 0) > 1
        and result["max_drawdown_pct"] <= 20
        for result in results.values()
    )
    output = {
        "passed": passed,
        "selection": "none; frozen before external block",
        "contract": CONTRACT,
        "costs": {"base_one_way_pct": BASE_ONE_WAY_COST_PCT, "stress_one_way_pct": STRESS_ONE_WAY_COST_PCT},
        "funding_included": True,
        "dataset": {"start": str(start), "end": str(end), "rows": len(close), "symbols": list(close.columns)},
        "results": results,
    }
    Path(args.out).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
