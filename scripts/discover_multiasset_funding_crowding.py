"""Discover a trend breakout portfolio with a simple funding-crowding veto."""
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
    for lookback, funding_threshold, top_n, max_concurrent in itertools.product(
        (2, 4), (0.0, 1.0, 2.0), (8, 12), (1, 2),
    ):
        contract = strategy.Contract(
            entry_mode="breakout_continuation", entry_timeframe="1h",
            breakout_lookback_bars=lookback, breakout_buffer_atr=.1,
            base_stop_atr=2, maximum_stop_atr=4, risk_reward=4,
            maximum_hold_hours=24, cooldown_hours=3,
            risk_per_episode_pct=.25, maximum_capital_fraction=.25,
            minimum_trend_strength=1, sentiment_policy="none",
        )
        if lookback not in feature_cache:
            feature_cache[lookback] = prepare(markets, sentiment, contract)
        gross = simulate(feature_cache[lookback], contract, *BOUNDS["train"],
                         top_n, max_concurrent, 0.0, 0.0, funding_threshold)
        base_trades, stress_trades = reprice(gross, BASE_COST_PCT), reprice(gross, STRESS_COST_PCT)
        grid.append({
            "contract": contract.manifest(), "top_n_liquid": top_n,
            "max_concurrent": max_concurrent,
            "funding_crowding_z": funding_threshold,
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
        featured = feature_cache[contract.breakout_lookback_bars]
        base, stress, trades = {}, {}, {}
        for name, bounds in BOUNDS.items():
            gross = simulate(featured, contract, *bounds, selected["top_n_liquid"],
                             selected["max_concurrent"], 0.0, 0.0,
                             selected["funding_crowding_z"])
            raw, stressed = reprice(gross, BASE_COST_PCT), reprice(gross, STRESS_COST_PCT)
            base[name], stress[name] = metrics(raw, *bounds), metrics(stressed, *bounds)
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
        "package_id": "multiasset_funding_crowding_v1",
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
    path = Path("data/backtests/multiasset_funding_crowding_5y.json")
    path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps({key: output[key] for key in (
        "passed", "status", "grid_size", "eligible_train", "selected", "base", "stress"
    )}, indent=2))


if __name__ == "__main__":
    main()
