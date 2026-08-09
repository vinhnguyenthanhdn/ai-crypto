"""Discover a simple multi-asset UTC-session momentum portfolio."""
import itertools
import json
from pathlib import Path

from src.engine import trend_sentiment as strategy
from scripts.discover_multiasset_trend_portfolio import (
    BASE_COST_PCT, BOUNDS, ROOT, STRESS_COST_PCT,
    load_universe, prepare, simulate,
)
from scripts.discover_trend_breakout_champion import reprice
from scripts.discover_trend_sentiment_champion import load_sentiment, metrics


def main() -> None:
    markets, manifest = load_universe()
    sentiment, sentiment_hash = load_sentiment(ROOT / "alternative_fng_history.json")
    grid, feature_cache = [], {}
    for volume_quantile, momentum_atr, risk_reward, max_concurrent in itertools.product(
        (.5, .75), (.1, .25, .5), (2, 3), (1, 2),
    ):
        contract = strategy.Contract(
            entry_mode="session_momentum", entry_timeframe="1h",
            session_hours=8, volume_lookback_bars=24 * 30,
            volume_quantile=volume_quantile, momentum_atr_min=momentum_atr,
            base_stop_atr=2, maximum_stop_atr=4, risk_reward=risk_reward,
            maximum_hold_hours=8, cooldown_hours=3,
            risk_per_episode_pct=.25, maximum_capital_fraction=.25,
            minimum_trend_strength=1, sentiment_policy="none",
        )
        key = (volume_quantile, momentum_atr)
        if key not in feature_cache:
            feature_cache[key] = prepare(markets, sentiment, contract)
        gross = simulate(feature_cache[key], contract, *BOUNDS["train"],
                         8, max_concurrent, 0.0, 0.0)
        base_trades = reprice(gross, BASE_COST_PCT)
        stress_trades = reprice(gross, STRESS_COST_PCT)
        grid.append({
            "contract": contract.manifest(), "top_n_liquid": 8,
            "max_concurrent": max_concurrent,
            "train": metrics(base_trades, *BOUNDS["train"]),
            "train_stress": metrics(stress_trades, *BOUNDS["train"]),
        })
    eligible = [row for row in grid if (
        5 <= row["train"]["entries_per_week"] <= 10
        and row["train"]["net_return_pct"] > 0
        and (row["train"]["profit_factor"] or 0) > 1.05
        and row["train"]["max_drawdown_pct"] <= 20
        and row["train_stress"]["net_return_pct"] > 0
        and (row["train_stress"]["profit_factor"] or 0) > 1
    )]
    selected = max(eligible, key=lambda row: row["train_stress"]["net_return_pct"]) if eligible else None
    base = stress = trades = {}
    if selected:
        contract = strategy.Contract(**selected["contract"])
        key = (contract.volume_quantile, contract.momentum_atr_min)
        base, stress, trades = {}, {}, {}
        for name, bounds in BOUNDS.items():
            gross = simulate(feature_cache[key], contract, *bounds, 8,
                             selected["max_concurrent"], 0.0, 0.0)
            raw = reprice(gross, BASE_COST_PCT)
            stressed = reprice(gross, STRESS_COST_PCT)
            base[name] = metrics(raw, *bounds)
            stress[name] = metrics(stressed, *bounds)
            trades[name] = [
                {key: value.isoformat() if hasattr(value, "isoformat") else value
                 for key, value in trade.items()} for trade in raw
            ]
    passed = bool(selected and all(
        5 <= base[name]["entries_per_week"] <= 10
        and base[name]["net_return_pct"] > 0 and (base[name]["profit_factor"] or 0) > 1
        and stress[name]["net_return_pct"] > 0 and (stress[name]["profit_factor"] or 0) > 1
        for name in ("validation", "test")
    ))
    output = {
        "passed": passed, "status": "RESEARCH_PASS" if passed else "REJECTED",
        "package_id": "multiasset_utc_session_momentum_v1",
        "selection": "train only", "bounds": {name: [str(value) for value in values]
                                                for name, values in BOUNDS.items()},
        "universe": sorted(markets), "source_manifest": manifest,
        "sentiment_sha256": sentiment_hash,
        "cost": {"base_round_trip_pct": BASE_COST_PCT,
                 "stress_round_trip_pct": STRESS_COST_PCT, "funding": "exact settlements"},
        "grid_size": len(grid), "eligible_train": len(eligible),
        "selected": selected, "base": base, "stress": stress,
        "trades": trades, "grid": grid,
    }
    path = Path("data/backtests/multiasset_utc_session_momentum_5y.json")
    path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps({key: output[key] for key in (
        "passed", "status", "grid_size", "eligible_train", "selected", "base", "stress"
    )}, indent=2))


if __name__ == "__main__":
    main()
