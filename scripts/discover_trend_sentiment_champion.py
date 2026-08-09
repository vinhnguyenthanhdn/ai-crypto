"""Discover and validate the user's long-trend/short-trend adaptive-stop strategy."""
import argparse
import gzip
import hashlib
import itertools
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.engine import trend_sentiment as strategy  # noqa: E402


BASE_ROUND_TRIP_COST_PCT = 0.14
STRESS_ROUND_TRIP_COST_PCT = 0.30
FNG_URL = "https://api.alternative.me/fng/?limit=0&format=json"


def load_market(path: Path) -> pd.DataFrame:
    with gzip.open(path, "rt", encoding="utf-8") as source:
        rows = json.load(source)["rows"]
    frame = pd.DataFrame(rows)
    frame["ts"] = pd.to_datetime(frame.ts, unit="ms", utc=True)
    return frame.set_index("ts").sort_index()


def load_sentiment(cache: Path) -> tuple[pd.DataFrame, str]:
    if cache.exists():
        raw = json.loads(cache.read_text(encoding="utf-8"))
    else:
        response = requests.get(FNG_URL, timeout=30)
        response.raise_for_status()
        raw = response.json()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    rows = pd.DataFrame(raw["data"])
    rows["ts"] = pd.to_datetime(rows.timestamp.astype(int), unit="s", utc=True)
    rows["sentiment_value"] = rows.value.astype(float)
    digest = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
    return rows.set_index("ts").sort_index(), digest


def simulate(frame: pd.DataFrame, symbol: str, contract: strategy.Contract,
             start, end, cost_pct: float) -> list[dict]:
    rows = frame[(frame.index >= start) & (frame.index < end)]
    trades, position, pending = [], None, None
    cooldown_until = pd.Timestamp.min.tz_localize("UTC")
    interval = pd.Timedelta(contract.entry_timeframe)
    for timestamp, row in rows.iterrows():
        if position is not None:
            adverse = row.low <= position["stop_price"] if position["side"] == "LONG" else row.high >= position["stop_price"]
            favorable = row.high >= position["take_profit_price"] if position["side"] == "LONG" else row.low <= position["take_profit_price"]
            exit_price = reason = None
            if adverse:
                exit_price, reason = position["stop_price"], "STOP_LOSS"
            elif favorable:
                exit_price, reason = position["take_profit_price"], "TAKE_PROFIT"
            elif timestamp - position["entry_time"] >= pd.Timedelta(hours=contract.maximum_hold_hours):
                exit_price, reason = float(row.open), "TIMEOUT"
            elif (position["side"] == "LONG" and row.trend_direction < 0) or (position["side"] == "SHORT" and row.trend_direction > 0):
                exit_price, reason = float(row.open), "TREND_FLIP"
            if exit_price is not None:
                direction_return = ((exit_price / position["entry_price"] - 1)
                                    if position["side"] == "LONG"
                                    else (position["entry_price"] / exit_price - 1))
                net = position["capital_fraction"] * (direction_return - cost_pct / 100)
                trades.append({**position, "exit_time": timestamp, "exit_price": float(exit_price),
                               "exit_reason": reason, "net_equity_return": float(net)})
                position = None
                cooldown_until = timestamp + pd.Timedelta(hours=contract.cooldown_hours)

        if pending is not None and position is None and timestamp >= pending["fill_time"]:
            entry = float(row.open)
            plan = strategy.position_plan(entry, pending["atr"], pending["side"], pending["trend_strength"], contract)
            position = {"symbol": symbol, "side": pending["side"], "signal_time": pending["signal_time"],
                        "entry_time": timestamp, "entry_price": entry, "sentiment_value": pending["sentiment_value"],
                        "trend_strength": pending["trend_strength"], **plan}
            pending = None

        if position is None and pending is None and timestamp >= cooldown_until:
            if bool(row.long_signal):
                side = "LONG"
            elif bool(row.short_signal):
                side = "SHORT"
            else:
                side = None
            if side and np.isfinite(row.entry_atr) and row.entry_atr > 0 and np.isfinite(row.trend_strength):
                pending = {"side": side, "signal_time": timestamp, "fill_time": timestamp + interval,
                           "atr": float(row.entry_atr), "trend_strength": float(row.trend_strength),
                           "sentiment_value": None if pd.isna(row.sentiment_value) else float(row.sentiment_value)}
    return trades


def metrics(trades: list[dict], start, end) -> dict:
    ordered = sorted(trades, key=lambda trade: trade["exit_time"])
    returns = np.asarray([trade["net_equity_return"] for trade in ordered], dtype=float)
    curve = np.cumprod(1 + returns) if len(returns) else np.asarray([1.0])
    peaks = np.maximum.accumulate(np.r_[1.0, curve])
    drawdown = np.r_[1.0, curve] / peaks - 1
    gains, losses = returns[returns > 0].sum(), -returns[returns < 0].sum()
    weeks = max((pd.Timestamp(end) - pd.Timestamp(start)).total_seconds() / (7 * 86400), 1 / 7)
    monthly = pd.Series(
        [trade["net_equity_return"] for trade in ordered],
        index=[trade["exit_time"] for trade in ordered], dtype=float,
    ).resample("30D").sum() if ordered else pd.Series(dtype=float)
    return {"entries": len(ordered), "entries_per_week": len(ordered) / weeks,
            "net_return_pct": (curve[-1] - 1) * 100 if len(returns) else 0.0,
            "profit_factor": gains / losses if losses > 0 else (None if gains == 0 else 999.0),
            "win_rate_pct": (returns > 0).mean() * 100 if len(returns) else 0.0,
            "max_drawdown_pct": abs(float(drawdown.min())) * 100,
            "positive_30d_ratio": float((monthly > 0).mean()) if len(monthly) else 0.0,
            "long_entries": sum(t["side"] == "LONG" for t in ordered),
            "short_entries": sum(t["side"] == "SHORT" for t in ordered)}


def evaluate(featured, contract, bounds, cost):
    by_split, raw = {}, {}
    for name, (start, end) in bounds.items():
        trades = []
        for symbol, frame in featured.items():
            trades.extend(simulate(frame, symbol, contract, start, end, cost))
        by_split[name] = metrics(trades, start, end)
        raw[name] = trades
    return by_split, raw


def serializable_trade(trade):
    return {key: (value.isoformat() if isinstance(value, pd.Timestamp) else value)
            for key, value in trade.items()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--btc", default="data/backtests/binance_btcusdt_spot_5m_flow_9y.json.gz")
    parser.add_argument("--eth", default="data/backtests/binance_ethusdt_spot_5m_flow_4y.json.gz")
    parser.add_argument("--sentiment-cache", default="data/backtests/alternative_fng_history.json")
    parser.add_argument("--out", default="data/backtests/trend_sentiment_adaptive_risk.json")
    args = parser.parse_args()
    markets = {"BTCUSDT": load_market(Path(args.btc)), "ETHUSDT": load_market(Path(args.eth))}
    sentiment, sentiment_hash = load_sentiment(Path(args.sentiment_cache))
    start = max(sentiment.index.min(), markets["BTCUSDT"].index.min())
    end = min(frame.index.max() for frame in markets.values()) + pd.Timedelta(minutes=5)
    train_end = pd.Timestamp("2023-08-07", tz="UTC")
    validation_end = pd.Timestamp("2024-08-07", tz="UTC")
    bounds = {"train": (start, train_end), "validation": (train_end, validation_end),
              "test": (validation_end, end)}

    contracts = []
    for values in itertools.product(("30min", "1h"), (10, 20), (1.5, 2.0),
                                    (3.0, 4.0), (1.5, 2.0), (24, 48),
                                    ("none", "contrarian_veto")):
        entry_tf, entry_ema, base_stop, max_stop, rr, hold, sentiment_policy = values
        contracts.append(strategy.Contract(entry_timeframe=entry_tf, entry_ema_bars=entry_ema,
                         base_stop_atr=base_stop, maximum_stop_atr=max_stop,
                         risk_reward=rr, maximum_hold_hours=hold,
                         sentiment_policy=sentiment_policy))
    feature_cache = {}
    grid = []
    for contract in contracts:
        feature_key = (contract.entry_timeframe, contract.entry_ema_bars,
                       contract.sentiment_policy)
        if feature_key not in feature_cache:
            feature_cache[feature_key] = {
                symbol: strategy.add_features(frame, sentiment, contract) for symbol, frame in markets.items()
            }
        result, _ = evaluate(feature_cache[feature_key], contract, {"train": bounds["train"]}, BASE_ROUND_TRIP_COST_PCT)
        grid.append({"contract": contract.manifest(), "train": result["train"]})
    eligible = [row for row in grid if row["train"]["net_return_pct"] > 0
                and (row["train"]["profit_factor"] or 0) > 1.05
                and 5 <= row["train"]["entries_per_week"] <= 10
                and row["train"]["max_drawdown_pct"] <= 20]
    selected_row = max(eligible, key=lambda row: row["train"]["net_return_pct"] /
                       max(row["train"]["max_drawdown_pct"], 0.1)) if eligible else None
    selected, base, stress, trades = None, {}, {}, {}
    if selected_row:
        selected = strategy.Contract(**selected_row["contract"])
        key = (selected.entry_timeframe, selected.entry_ema_bars, selected.sentiment_policy)
        base, raw = evaluate(feature_cache[key], selected, bounds, BASE_ROUND_TRIP_COST_PCT)
        stress, _ = evaluate(feature_cache[key], selected, bounds, STRESS_ROUND_TRIP_COST_PCT)
        trades = {name: [serializable_trade(t) for t in values] for name, values in raw.items()}
    passed = bool(selected and all(base[name]["net_return_pct"] > 0
                  and (base[name]["profit_factor"] or 0) > 1
                  and 5 <= base[name]["entries_per_week"] <= 10
                  and stress[name]["net_return_pct"] > 0
                  and (stress[name]["profit_factor"] or 0) > 1
                  for name in ("validation", "test")))
    output = {"passed": passed, "status": "RESEARCH_PASS" if passed else "REJECTED",
              "package_id": strategy.PACKAGE_ID,
              "dataset": {"start": str(start), "train_end": str(train_end),
                          "validation_end": str(validation_end), "end": str(end),
                          "symbols": list(markets), "sentiment_source": FNG_URL,
                          "sentiment_sha256": sentiment_hash},
              "cost": {"base_round_trip_pct": BASE_ROUND_TRIP_COST_PCT,
                       "stress_round_trip_pct": STRESS_ROUND_TRIP_COST_PCT},
              "grid_size": len(grid), "eligible_train": len(eligible),
              "selected": selected.manifest() if selected else None,
              "base": base, "stress": stress, "trades": trades, "grid": grid}
    Path(args.out).write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps({key: output[key] for key in ("passed", "status", "grid_size", "eligible_train", "selected", "base", "stress")}, indent=2))


if __name__ == "__main__":
    main()
