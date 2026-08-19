"""Trade-for-trade parity between frozen production core and research ledger."""
import json

import numpy as np
import pandas as pd

from src.engine import funding_crowding as strategy
from scripts.discover_multiasset_trend_portfolio import (
    BOUNDS, ROOT, close_trade, load_universe, prepare,
)
from scripts.discover_trend_sentiment_champion import load_sentiment


BASE_COST_PCT = .07


def replay(featured: dict[str, pd.DataFrame], start: pd.Timestamp,
           end: pd.Timestamp) -> list[dict]:
    contract = strategy.FROZEN_CONTRACT
    sliced = {symbol: frame[(frame.index >= start) & (frame.index < end)]
              for symbol, frame in featured.items()}
    timestamps = sorted(set().union(*(frame.index for frame in sliced.values())))
    positions, pending, trades = {}, {}, []
    cooldown = {symbol: pd.Timestamp.min.tz_localize("UTC") for symbol in sliced}
    for timestamp in timestamps:
        rows = {symbol: frame.loc[timestamp] for symbol, frame in sliced.items()
                if timestamp in frame.index}
        for symbol, position in list(positions.items()):
            if symbol not in rows:
                continue
            decision = strategy.exit_decision(position, rows[symbol], timestamp)
            if decision is None:
                continue
            exit_price, reason = decision
            trades.append(close_trade(position, rows[symbol], timestamp,
                                      exit_price, reason, BASE_COST_PCT))
            del positions[symbol]
            cooldown[symbol] = timestamp + pd.Timedelta(hours=contract.cooldown_hours)

        for symbol, order in list(pending.items()):
            if timestamp < order["fill_time"]:
                continue
            if symbol not in rows or len(positions) >= contract.max_concurrent:
                del pending[symbol]
                continue
            row = rows[symbol]
            entry_price = float(row.open)
            plan = strategy.entry_plan(entry_price, order)
            positions[symbol] = {
                "symbol": symbol, "side": order["side"],
                "signal_time": order["signal_time"], "entry_time": timestamp,
                "entry_price": entry_price,
                "entry_funding": float(row.cumulative_funding),
                "sentiment_value": order["sentiment_value"],
                "market_breadth": 0.0, "signal_funding_z": order["funding_z"],
                **plan,
            }
            del pending[symbol]

        slots = contract.max_concurrent - len(positions) - len(pending)
        if slots <= 0:
            continue
        blocked = set(positions) | set(pending) | {
            symbol for symbol, until in cooldown.items() if timestamp < until
        }
        for decision in strategy.rank_entries(rows, blocked, slots):
            row = rows[decision["symbol"]]
            pending[decision["symbol"]] = {
                **decision, "signal_time": timestamp,
                "fill_time": timestamp + pd.Timedelta(hours=1),
                "sentiment_value": None if pd.isna(row.sentiment_value)
                else float(row.sentiment_value),
            }
    return trades


def normalize(trade: dict) -> dict:
    timestamp_keys = ("signal_time", "entry_time", "exit_time")
    float_keys = ("entry_price", "stop_price", "take_profit_price", "exit_price",
                  "capital_fraction", "net_equity_return", "funding_return")
    result = {key: trade.get(key) for key in (
        "symbol", "side", *timestamp_keys, *float_keys, "exit_reason",
    )}
    for key in timestamp_keys:
        result[key] = pd.Timestamp(result[key]).isoformat()
    for key in float_keys:
        result[key] = float(result[key])
    return result


def compare(reference: list[dict], actual: list[dict]) -> list[dict]:
    ordering = lambda item: (item["entry_time"], item["symbol"], item["side"])
    expected = sorted((normalize(item) for item in reference), key=ordering)
    observed = sorted((normalize(item) for item in actual), key=ordering)
    mismatches = []
    if len(expected) != len(observed):
        mismatches.append({"field": "trade_count", "expected": len(expected),
                           "actual": len(observed)})
    for index, (left, right) in enumerate(zip(expected, observed)):
        for key in left:
            if key in ("entry_price", "stop_price", "take_profit_price", "exit_price",
                       "capital_fraction", "net_equity_return", "funding_return"):
                equal = np.isclose(left[key], right[key], atol=1e-12, rtol=0)
            else:
                equal = left[key] == right[key]
            if not equal:
                mismatches.append({"trade": index, "field": key,
                                   "expected": left[key], "actual": right[key]})
                if len(mismatches) >= 100:
                    return mismatches
    return mismatches


def main() -> None:
    reference = json.loads((ROOT / "multiasset_funding_crowding_5y.json").read_text())
    markets, _ = load_universe()
    sentiment, _ = load_sentiment(ROOT / "alternative_fng_history.json")
    featured = prepare(markets, sentiment, strategy.FROZEN_CONTRACT.trend_contract())
    results = {}
    all_passed = True
    for split, bounds in BOUNDS.items():
        actual = replay(featured, *bounds)
        mismatches = compare(reference["trades"][split], actual)
        results[split] = {
            "reference_trades": len(reference["trades"][split]),
            "production_trades": len(actual), "passed": not mismatches,
            "mismatches": mismatches,
        }
        all_passed &= not mismatches
    output = {
        "passed": all_passed, "strategy_package_id": strategy.PACKAGE_ID,
        "contract": strategy.FROZEN_CONTRACT.manifest(), "splits": results,
    }
    path = ROOT / "funding_crowding_runtime_parity_5y.json"
    path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output, indent=2))
    if not all_passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
