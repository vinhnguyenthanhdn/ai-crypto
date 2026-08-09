"""Stress frozen slow-pullback rule theo cost, stop và calendar year."""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.analyze_slow_donchian_entry import _load_4h, _metrics  # noqa: E402
from scripts.analyze_slow_mean_reversion_entry import _run_segment  # noqa: E402


def _adjust_cost(trades, total_cost):
    extra = total_cost - 0.30
    return [{**trade, "net_return_pct": trade["net_return_pct"] - extra} for trade in trades]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--flow-cache", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    bars = _load_4h(Path(args.flow_cache))
    bars["ema60"] = bars.close.ewm(span=60, adjust=False).mean()
    bars["ema180"] = bars.close.ewm(span=180, adjust=False).mean()
    bars["ema180_slope"] = bars.ema180.pct_change(30)
    start, end = bars.index[0], bars.index[-1] + pd.Timedelta(hours=4)
    train_end, validation_end = start + pd.Timedelta(days=1460), start + pd.Timedelta(days=1825)
    splits = {"train": (start, train_end), "validation": (train_end, validation_end), "test": (validation_end, end)}
    stress = {}
    for stop in (3.0, 5.0, 8.0, None):
        key = "none" if stop is None else str(stop)
        base = {name: _run_segment(bars, *bounds, 30, 2.0, stop, "PRICE_EMA180") for name, bounds in splits.items()}
        stress[key] = {
            str(cost): {name: _metrics(_adjust_cost(trades, cost)) for name, trades in base.items()}
            for cost in (0.30, 0.40, 0.50, 0.70)
        }
    yearly = {}
    cursor = start
    while cursor < end:
        boundary = min(cursor + pd.DateOffset(years=1), end)
        trades = _run_segment(bars, cursor, boundary, 30, 2.0, 8.0, "PRICE_EMA180")
        yearly[f"{cursor.date()}_{boundary.date()}"] = _metrics(trades)
        cursor = boundary
    output = {"contract": {"entry_rule": "4h z30 >= 2 pullback in price-vs-EMA180 trend", "baseline_cost_pct": 0.30, "stressed_cost_pct": [0.40, 0.50, 0.70], "stops_atr": [3, 5, 8, None]}, "stress": stress, "yearly_stop8_cost03": yearly}
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
