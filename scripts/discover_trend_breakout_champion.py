"""Search a causal trend-aligned breakout family with cost/holdout gates."""
import argparse
import itertools
import json
from pathlib import Path

import pandas as pd

from src.engine import trend_sentiment as strategy
from scripts.discover_trend_sentiment_champion import (
    evaluate, load_market, load_sentiment, metrics, serializable_trade,
)


BASE_COST_PCT = 0.07
STRESS_COST_PCT = 0.14


def reprice(trades: list[dict], cost_pct: float) -> list[dict]:
    """Apply a round-trip cost to a gross trade ledger without replaying."""
    return [
        {**trade, "net_equity_return": trade["net_equity_return"]
         - trade["capital_fraction"] * cost_pct / 100}
        for trade in trades
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--btc", default="data/backtests/binance_btcusdt_spot_5m_flow_9y.json.gz")
    parser.add_argument("--eth", default="data/backtests/binance_ethusdt_spot_5m_flow_4y.json.gz")
    parser.add_argument("--sentiment-cache", default="data/backtests/alternative_fng_history.json")
    parser.add_argument("--out", default="data/backtests/trend_breakout_adaptive_risk_v3.json")
    args = parser.parse_args()
    markets = {"BTCUSDT": load_market(Path(args.btc)),
               "ETHUSDT": load_market(Path(args.eth))}
    sentiment, sentiment_hash = load_sentiment(Path(args.sentiment_cache))
    start = max(sentiment.index.min(), markets["BTCUSDT"].index.min())
    end = min(frame.index.max() for frame in markets.values()) + pd.Timedelta(minutes=5)
    bounds = {
        "train": (start, pd.Timestamp("2023-08-07", tz="UTC")),
        "validation": (pd.Timestamp("2023-08-07", tz="UTC"), pd.Timestamp("2024-08-07", tz="UTC")),
        "test": (pd.Timestamp("2024-08-07", tz="UTC"), end),
    }

    contracts = []
    for values in itertools.product(
        (1.5, 2.0), (4.0, 5.0), (2.0, 3.0, 4.0),
        (24, 48, 72), (3, 6),
    ):
        base_stop, max_stop, risk_reward, hold, cooldown = values
        contracts.append(strategy.Contract(
            entry_mode="breakout_continuation", entry_timeframe="30min",
            breakout_lookback_bars=4, breakout_buffer_atr=0.1,
            base_stop_atr=base_stop, maximum_stop_atr=max_stop,
            risk_reward=risk_reward, maximum_hold_hours=hold,
            cooldown_hours=cooldown, sentiment_policy="none",
            minimum_trend_strength=1.0,
        ))

    feature_cache = {}
    grid = []
    for contract in contracts:
        key = (contract.entry_timeframe, contract.breakout_lookback_bars,
               contract.breakout_buffer_atr, contract.minimum_trend_strength,
               contract.sentiment_policy)
        if key not in feature_cache:
            feature_cache[key] = {
                symbol: strategy.add_features(frame, sentiment, contract)
                for symbol, frame in markets.items()
            }
        _, raw = evaluate(feature_cache[key], contract, {"train": bounds["train"]}, 0.0)
        gross_trades = raw["train"]
        grid.append({
            "contract": contract.manifest(),
            "train": metrics(reprice(gross_trades, BASE_COST_PCT), *bounds["train"]),
            "train_stress": metrics(reprice(gross_trades, STRESS_COST_PCT), *bounds["train"]),
        })

    eligible = [row for row in grid if (
        5 <= row["train"]["entries_per_week"] <= 10
        and row["train"]["net_return_pct"] > 0
        and (row["train"]["profit_factor"] or 0) > 1.05
        and row["train"]["max_drawdown_pct"] <= 20
        and row["train_stress"]["net_return_pct"] > 0
        and (row["train_stress"]["profit_factor"] or 0) > 1
    )]
    selected_row = max(
        eligible,
        key=lambda row: row["train_stress"]["net_return_pct"] /
        max(row["train_stress"]["max_drawdown_pct"], .1),
    ) if eligible else None
    selected = strategy.Contract(**selected_row["contract"]) if selected_row else None
    base = stress = trades = {}
    if selected:
        key = (selected.entry_timeframe, selected.breakout_lookback_bars,
               selected.breakout_buffer_atr, selected.minimum_trend_strength,
               selected.sentiment_policy)
        _, raw = evaluate(feature_cache[key], selected, bounds, 0.0)
        base = {name: metrics(reprice(values, BASE_COST_PCT), *bounds[name])
                for name, values in raw.items()}
        stress = {name: metrics(reprice(values, STRESS_COST_PCT), *bounds[name])
                  for name, values in raw.items()}
        trades = {name: [serializable_trade(item) for item in reprice(values, BASE_COST_PCT)]
                  for name, values in raw.items()}
    passed = bool(selected and all(
        5 <= base[name]["entries_per_week"] <= 10
        and base[name]["net_return_pct"] > 0
        and (base[name]["profit_factor"] or 0) > 1
        and stress[name]["net_return_pct"] > 0
        and (stress[name]["profit_factor"] or 0) > 1
        for name in ("validation", "test")
    ))
    output = {
        "passed": passed, "status": "RESEARCH_PASS" if passed else "REJECTED",
        "package_id": "trend_breakout_adaptive_risk_v3",
        "dataset": {"start": str(start), "train_end": str(bounds["validation"][0]),
                    "validation_end": str(bounds["test"][0]), "end": str(end),
                    "symbols": list(markets), "sentiment_sha256": sentiment_hash},
        "cost": {"base_round_trip_pct": BASE_COST_PCT,
                 "stress_round_trip_pct": STRESS_COST_PCT},
        "grid_size": len(grid), "eligible_train": len(eligible),
        "selected": selected.manifest() if selected else None,
        "base": base, "stress": stress, "trades": trades, "grid": grid,
    }
    Path(args.out).write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps({key: output[key] for key in (
        "passed", "status", "grid_size", "eligible_train", "selected", "base", "stress"
    )}, indent=2))


if __name__ == "__main__":
    main()
