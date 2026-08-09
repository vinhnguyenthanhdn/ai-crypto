"""Point-in-time liquid-universe portfolio for causal trend breakouts."""
import gzip
import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.engine import trend_sentiment as strategy
from scripts.discover_trend_sentiment_champion import load_sentiment, metrics


ROOT = Path("data/backtests")
BASE_COST_PCT = .07
STRESS_COST_PCT = .14
BOUNDS = {
    "train": (pd.Timestamp("2021-08-07", tz="UTC"), pd.Timestamp("2023-08-07", tz="UTC")),
    "validation": (pd.Timestamp("2023-08-07", tz="UTC"), pd.Timestamp("2024-08-07", tz="UTC")),
    "test": (pd.Timestamp("2024-08-07", tz="UTC"), pd.Timestamp("2026-08-07", tz="UTC")),
}


def load_rows(path: Path) -> tuple[pd.DataFrame, dict]:
    with gzip.open(path, "rt", encoding="utf-8") as source:
        raw = json.load(source)
    frame = pd.DataFrame(raw["rows"])
    frame["ts"] = pd.to_datetime(frame.ts, unit="ms", utc=True)
    return frame.set_index("ts"), raw["metadata"]


def load_universe() -> tuple[dict[str, pd.DataFrame], dict]:
    markets, manifest = {}, {}
    for recent in sorted((ROOT / "cross_sectional").glob("*_1h_3y.json.gz")):
        symbol = recent.name.split("_")[0].upper()
        older = ROOT / "cross_sectional_external" / recent.name.replace("3y", "2y")
        if not older.exists():
            continue
        old_frame, old_meta = load_rows(older)
        new_frame, new_meta = load_rows(recent)
        markets[symbol] = pd.concat([old_frame, new_frame]).sort_index()
        markets[symbol] = markets[symbol][~markets[symbol].index.duplicated(keep="last")]
        manifest[symbol] = {"older": old_meta, "recent": new_meta}
    return markets, manifest


def load_funding(symbol: str) -> pd.Series:
    pieces = []
    for folder, suffix in (("cross_sectional_external", "2y"), ("cross_sectional", "3y")):
        path = ROOT / folder / f"{symbol.lower()}_funding_{suffix}.json.gz"
        if not path.exists():
            continue
        frame, _ = load_rows(path)
        pieces.append(frame.funding_rate)
    if not pieces:
        return pd.Series(dtype=float)
    series = pd.concat(pieces).sort_index()
    return series[~series.index.duplicated(keep="last")]


def prepare(markets: dict[str, pd.DataFrame], sentiment: pd.DataFrame,
            contract: strategy.Contract) -> dict[str, pd.DataFrame]:
    featured = {}
    for symbol, market in markets.items():
        frame = strategy.add_features(market, sentiment, contract)
        frame["trailing_dollar_volume"] = (
            (frame.close * frame.volume).shift(1).rolling(24 * 30, min_periods=24 * 7).mean()
        )
        funding = load_funding(symbol).rename("funding_rate")
        if funding.empty:
            frame["cumulative_funding"] = 0.0
            frame["funding_z"] = 0.0
        else:
            funding_mean = funding.shift(1).rolling(21, min_periods=7).mean()
            funding_std = funding.shift(1).rolling(21, min_periods=7).std().replace(0, np.nan)
            funding_features = pd.DataFrame({
                "cumulative_funding": funding.cumsum(),
                "funding_z": (funding - funding_mean) / funding_std,
            })
            left = frame.reset_index().rename(columns={frame.index.name or "index": "ts"})
            right = funding_features.reset_index().rename(
                columns={funding_features.index.name or "index": "ts"}
            )
            frame = pd.merge_asof(left.sort_values("ts"), right.sort_values("ts"),
                                  on="ts", direction="backward").set_index("ts")
            frame["cumulative_funding"] = frame.cumulative_funding.fillna(0.0)
        featured[symbol] = frame
    return featured


def close_trade(position: dict, row: pd.Series, timestamp: pd.Timestamp,
                exit_price: float, reason: str, cost_pct: float) -> dict:
    direction = 1 if position["side"] == "LONG" else -1
    price_return = direction * (exit_price / position["entry_price"] - 1)
    funding_delta = float(row.cumulative_funding) - position["entry_funding"]
    funding_return = -direction * funding_delta
    net = position["capital_fraction"] * (
        price_return + funding_return - cost_pct / 100
    )
    return {**position, "exit_time": timestamp, "exit_price": float(exit_price),
            "exit_reason": reason, "funding_return": funding_return,
            "net_equity_return": float(net)}


def market_breadth(rows: dict[str, pd.Series], liquid: list[tuple[str, float]]) -> tuple[float, float]:
    """Return causal up/down ratios for the current liquid universe."""
    directions = [float(rows[symbol].trend_direction) for symbol, _ in liquid
                  if np.isfinite(rows[symbol].trend_direction)]
    if not directions:
        return 0.0, 0.0
    return (sum(value > 0 for value in directions) / len(directions),
            sum(value < 0 for value in directions) / len(directions))


def funding_allows(side: str, funding_z: float, threshold: float) -> bool:
    """Veto a direction when same-side perpetual positioning is crowded."""
    if not np.isfinite(funding_z):
        return False
    if side == "LONG":
        return funding_z <= threshold
    if side == "SHORT":
        return funding_z >= -threshold
    raise ValueError("side must be LONG or SHORT")


def simulate(featured: dict[str, pd.DataFrame], contract: strategy.Contract,
             start: pd.Timestamp, end: pd.Timestamp, top_n: int,
             max_concurrent: int, breadth_threshold: float,
             cost_pct: float, funding_crowding_threshold: float | None = None,
             close_at_end: bool = False) -> list[dict]:
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
            row = rows[symbol]
            adverse = row.low <= position["stop_price"] if position["side"] == "LONG" else row.high >= position["stop_price"]
            favorable = row.high >= position["take_profit_price"] if position["side"] == "LONG" else row.low <= position["take_profit_price"]
            exit_price = reason = None
            if adverse:
                exit_price, reason = position["stop_price"], "STOP_LOSS"
            elif favorable:
                exit_price, reason = position["take_profit_price"], "TAKE_PROFIT"
            elif timestamp - position["entry_time"] >= pd.Timedelta(hours=contract.maximum_hold_hours):
                exit_price, reason = float(row.open), "TIMEOUT"
            elif ((position["side"] == "LONG" and row.trend_direction < 0)
                  or (position["side"] == "SHORT" and row.trend_direction > 0)):
                exit_price, reason = float(row.open), "TREND_FLIP"
            if exit_price is not None:
                trades.append(close_trade(position, row, timestamp, exit_price, reason, cost_pct))
                del positions[symbol]
                cooldown[symbol] = timestamp + pd.Timedelta(hours=contract.cooldown_hours)

        for symbol, order in list(pending.items()):
            if timestamp < order["fill_time"]:
                continue
            if symbol not in rows or len(positions) >= max_concurrent:
                del pending[symbol]
                continue
            row = rows[symbol]
            entry = float(row.open)
            plan = strategy.position_plan(entry, order["atr"], order["side"],
                                          order["trend_strength"], contract)
            positions[symbol] = {
                "symbol": symbol, "side": order["side"],
                "signal_time": order["signal_time"], "entry_time": timestamp,
                "entry_price": entry, "entry_funding": float(row.cumulative_funding),
                "sentiment_value": order["sentiment_value"], **plan,
                "market_breadth": order["market_breadth"],
                "signal_funding_z": order["funding_z"],
            }
            del pending[symbol]

        free_slots = max_concurrent - len(positions) - len(pending)
        if free_slots <= 0:
            continue
        liquid = sorted(
            ((symbol, float(row.trailing_dollar_volume)) for symbol, row in rows.items()
             if np.isfinite(row.trailing_dollar_volume)),
            key=lambda item: item[1], reverse=True,
        )[:top_n]
        up_breadth, down_breadth = market_breadth(rows, liquid)
        candidates = []
        for symbol, _ in liquid:
            row = rows[symbol]
            if symbol in positions or symbol in pending or timestamp < cooldown[symbol]:
                continue
            side = "LONG" if bool(row.long_signal) else "SHORT" if bool(row.short_signal) else None
            directional_breadth = up_breadth if side == "LONG" else down_breadth
            if side and directional_breadth < breadth_threshold:
                continue
            if side and funding_crowding_threshold is not None and not funding_allows(
                side, float(row.funding_z), funding_crowding_threshold
            ):
                continue
            if side and np.isfinite(row.entry_atr) and np.isfinite(row.trend_strength):
                candidates.append((float(row.trend_strength), symbol, side,
                                   directional_breadth, row))
        for _, symbol, side, directional_breadth, row in sorted(
            candidates, reverse=True
        )[:free_slots]:
            pending[symbol] = {
                "side": side, "signal_time": timestamp,
                "fill_time": timestamp + pd.Timedelta(hours=1),
                "atr": float(row.entry_atr), "trend_strength": float(row.trend_strength),
                "sentiment_value": None if pd.isna(row.sentiment_value) else float(row.sentiment_value),
                "market_breadth": directional_breadth,
                "funding_z": None if funding_crowding_threshold is None else float(row.funding_z),
            }
    if close_at_end:
        for symbol, position in list(positions.items()):
            available = sliced[symbol][sliced[symbol].index < end]
            if available.empty:
                continue
            timestamp, row = available.index[-1], available.iloc[-1]
            trades.append(close_trade(
                position, row, timestamp, float(row.close), "WINDOW_END_MARK", cost_pct
            ))
    return trades


def main() -> None:
    markets, manifest = load_universe()
    sentiment, sentiment_hash = load_sentiment(ROOT / "alternative_fng_history.json")
    grid = []
    feature_cache = {}
    for lookback, top_n, max_concurrent, breadth_threshold in itertools.product(
        (2, 4), (8, 12), (1, 2), (.5, .625, .75),
    ):
        contract = strategy.Contract(
            entry_mode="breakout_continuation", entry_timeframe="1h",
            breakout_lookback_bars=lookback, breakout_buffer_atr=.1,
            base_stop_atr=2, maximum_stop_atr=4, risk_reward=4,
            maximum_hold_hours=24, cooldown_hours=3,
            risk_per_episode_pct=.25, maximum_capital_fraction=.25,
            minimum_trend_strength=1, sentiment_policy="none",
        )
        key = (lookback, "none")
        if key not in feature_cache:
            feature_cache[key] = prepare(markets, sentiment, contract)
        base_trades = simulate(feature_cache[key], contract, *BOUNDS["train"],
                               top_n, max_concurrent, breadth_threshold, BASE_COST_PCT)
        stress_trades = simulate(feature_cache[key], contract, *BOUNDS["train"],
                                 top_n, max_concurrent, breadth_threshold, STRESS_COST_PCT)
        grid.append({
            "contract": contract.manifest(), "top_n_liquid": top_n,
            "max_concurrent": max_concurrent,
            "breadth_threshold": breadth_threshold,
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
    selected_row = (
        max(eligible, key=lambda row: row["train_stress"]["net_return_pct"])
        if eligible else None
    )
    base = stress = trades = {}
    if selected_row:
        contract = strategy.Contract(**selected_row["contract"])
        key = (contract.breakout_lookback_bars, contract.sentiment_policy)
        base, stress, trades = {}, {}, {}
        for name, bounds in BOUNDS.items():
            raw = simulate(feature_cache[key], contract, *bounds,
                           selected_row["top_n_liquid"], selected_row["max_concurrent"],
                           selected_row["breadth_threshold"],
                           BASE_COST_PCT)
            stressed = simulate(feature_cache[key], contract, *bounds,
                                selected_row["top_n_liquid"], selected_row["max_concurrent"],
                                selected_row["breadth_threshold"],
                                STRESS_COST_PCT)
            base[name] = metrics(raw, *bounds)
            stress[name] = metrics(stressed, *bounds)
            trades[name] = [{key: value.isoformat() if isinstance(value, pd.Timestamp) else value
                             for key, value in trade.items()} for trade in raw]
    passed = bool(selected_row and all(
        5 <= base[name]["entries_per_week"] <= 10
        and base[name]["net_return_pct"] > 0 and (base[name]["profit_factor"] or 0) > 1
        and stress[name]["net_return_pct"] > 0 and (stress[name]["profit_factor"] or 0) > 1
        for name in ("validation", "test")
    ))
    output = {
        "passed": passed, "status": "RESEARCH_PASS" if passed else "REJECTED",
        "package_id": "multiasset_trend_breadth_portfolio_v2",
        "selection": "train only", "bounds": {name: [str(v) for v in values]
                                                for name, values in BOUNDS.items()},
        "universe": sorted(markets), "source_manifest": manifest,
        "sentiment_sha256": sentiment_hash,
        "cost": {"base_round_trip_pct": BASE_COST_PCT,
                 "stress_round_trip_pct": STRESS_COST_PCT, "funding": "exact settlements"},
        "grid_size": len(grid), "eligible_train": len(eligible),
        "selected": selected_row, "base": base, "stress": stress,
        "trades": trades, "grid": grid,
    }
    out = ROOT / "multiasset_trend_breadth_portfolio_5y.json"
    out.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps({key: output[key] for key in (
        "passed", "status", "grid_size", "eligible_train", "selected", "base", "stress"
    )}, indent=2))


if __name__ == "__main__":
    main()
