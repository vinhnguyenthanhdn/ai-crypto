"""Train-only discovery for high-volume session momentum inside slow trend."""
import json
from pathlib import Path

import pandas as pd

from discover_trend_sentiment_champion import (
    BASE_ROUND_TRIP_COST_PCT, STRESS_ROUND_TRIP_COST_PCT, evaluate,
    load_market, load_sentiment,
)
from src.engine import trend_sentiment as strategy


def main():
    markets = {
        "BTCUSDT": load_market(Path("data/backtests/binance_btcusdt_spot_5m_flow_9y.json.gz")),
        "ETHUSDT": load_market(Path("data/backtests/binance_ethusdt_spot_5m_flow_4y.json.gz")),
    }
    sentiment, sentiment_hash = load_sentiment(Path("data/backtests/alternative_fng_history.json"))
    start = max(sentiment.index.min(), markets["BTCUSDT"].index.min())
    end = min(frame.index.max() for frame in markets.values()) + pd.Timedelta(minutes=5)
    bounds = {
        "train": (start, pd.Timestamp("2023-08-07", tz="UTC")),
        "validation": (pd.Timestamp("2023-08-07", tz="UTC"), pd.Timestamp("2024-08-07", tz="UTC")),
        "test": (pd.Timestamp("2024-08-07", tz="UTC"), end),
    }
    grid, feature_cache = [], {}
    for session_hours in (4, 6):
        for quantile in (.70, .80, .90):
            for momentum in (.10, .25):
                for stops in ((1.5, 3.0), (1.5, 4.0), (2.0, 4.0)):
                    for reward in (1.5, 2.0):
                        contract = strategy.Contract(
                            entry_mode="session_momentum", entry_timeframe="30min",
                            entry_ema_bars=20, session_hours=session_hours,
                            volume_quantile=quantile, momentum_atr_min=momentum,
                            base_stop_atr=stops[0], maximum_stop_atr=stops[1],
                            risk_reward=reward, maximum_hold_hours=24,
                            sentiment_policy="contrarian_veto",
                        )
                        feature_key = (session_hours, quantile, momentum)
                        if feature_key not in feature_cache:
                            feature_cache[feature_key] = {
                                symbol: strategy.add_features(frame, sentiment, contract)
                                for symbol, frame in markets.items()
                            }
                        featured = feature_cache[feature_key]
                        train, _ = evaluate(featured, contract, {"train": bounds["train"]}, BASE_ROUND_TRIP_COST_PCT)
                        grid.append({"contract": contract.manifest(), "train": train["train"]})
    eligible = [row for row in grid if row["train"]["net_return_pct"] > 0
                and (row["train"]["profit_factor"] or 0) > 1.05
                and 5 <= row["train"]["entries_per_week"] <= 10
                and row["train"]["max_drawdown_pct"] <= 20]
    chosen = max(eligible, key=lambda row: row["train"]["net_return_pct"] /
                 max(row["train"]["max_drawdown_pct"], .1)) if eligible else None
    base = stress = {}
    if chosen:
        contract = strategy.Contract(**chosen["contract"])
        featured = feature_cache[(contract.session_hours, contract.volume_quantile,
                                  contract.momentum_atr_min)]
        base, _ = evaluate(featured, contract, bounds, BASE_ROUND_TRIP_COST_PCT)
        stress, _ = evaluate(featured, contract, bounds, STRESS_ROUND_TRIP_COST_PCT)
    passed = bool(chosen and all(base[name]["net_return_pct"] > 0
                  and (base[name]["profit_factor"] or 0) > 1
                  and 5 <= base[name]["entries_per_week"] <= 10
                  and stress[name]["net_return_pct"] > 0
                  and (stress[name]["profit_factor"] or 0) > 1
                  for name in ("validation", "test")))
    output = {"passed": passed, "status": "RESEARCH_PASS" if passed else "REJECTED",
              "package_id": strategy.PACKAGE_ID, "sentiment_sha256": sentiment_hash,
              "grid_size": len(grid), "eligible_train": len(eligible),
              "selected": chosen["contract"] if chosen else None,
              "base": base, "stress": stress, "grid": grid}
    Path("data/backtests/trend_session_adaptive_risk.json").write_text(
        json.dumps(output, indent=2), encoding="utf-8",
    )
    print(json.dumps({key: output[key] for key in
          ("passed", "status", "grid_size", "eligible_train", "selected", "base", "stress")}, indent=2))


if __name__ == "__main__":
    main()
