"""Frozen composite diagnostic on the last 30 fully closed UTC days by venue."""
import argparse
import gzip
import hashlib
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd

from scripts.discover_multiasset_trend_portfolio import simulate
from scripts.validate_composite_trend_champion import (
    composite_returns, portfolio_metrics,
)
from scripts.validate_funding_crowding_parity import compare as parity_compare
from scripts.validate_funding_crowding_parity import replay as production_replay
from src.engine import btc_spot_trend
from src.engine import funding_crowding
from src.engine import trend_sentiment


ASSETS = ("ADA", "AVAX", "BCH", "BNB", "BTC", "DOGE", "DOT", "ETC",
          "ETH", "FIL", "LINK", "LTC", "SOL", "TRX", "XRP")
VENUES = {
    "binance": {"swap": "binanceusdm", "spot": "binance"},
    "okx": {"swap": "okx", "spot": "okx"},
}
FAST_COST = {"base": .07, "stress": .14}
BTC_COST = {"base": .12, "stress": .24}
ROOT = Path("data/backtests")
CACHE = ROOT / "recent_composite_venues_30d_input.json.gz"
OUTPUT = ROOT / "recent_composite_venues_30d.json"


def fetch_ohlcv_range(exchange, symbol: str, timeframe: str,
                      start: pd.Timestamp, end: pd.Timestamp) -> list[list[float]]:
    frame_ms = exchange.parse_timeframe(timeframe) * 1000
    cursor, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    rows = {}
    while cursor < end_ms:
        batch = exchange.fetch_ohlcv(symbol, timeframe, since=cursor, limit=300)
        if not batch:
            break
        for row in batch:
            timestamp = int(row[0])
            if cursor <= timestamp and timestamp + frame_ms <= end_ms:
                rows[timestamp] = [timestamp, *map(float, row[1:6])]
        next_cursor = int(batch[-1][0]) + frame_ms
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if int(batch[-1][0]) >= end_ms:
            break
    return [rows[key] for key in sorted(rows)]


def fetch_funding_range(exchange, symbol: str, start: pd.Timestamp,
                        end: pd.Timestamp) -> list[list[float]]:
    cursor, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    rows = {}
    while cursor < end_ms:
        batch = exchange.fetch_funding_rate_history(symbol, since=cursor, limit=100)
        if not batch:
            break
        for item in batch:
            timestamp = int(item["timestamp"])
            if cursor <= timestamp < end_ms:
                rows[timestamp] = [timestamp, float(item["fundingRate"])]
        next_cursor = max(int(item["timestamp"]) for item in batch) + 1
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if next_cursor >= end_ms or len(batch) < 100:
            break
    return [rows[key] for key in sorted(rows)]


def fetch_venue(name: str, contract: dict, warmup: pd.Timestamp,
                funding_start: pd.Timestamp, end: pd.Timestamp) -> tuple[str, dict]:
    swap = getattr(ccxt, contract["swap"])({"enableRateLimit": True})
    spot = getattr(ccxt, contract["spot"])({"enableRateLimit": True})
    swap.load_markets()
    spot.load_markets()
    markets, funding = {}, {}
    for asset in ASSETS:
        symbol = f"{asset}/USDT:USDT"
        if symbol not in swap.markets or not swap.markets[symbol].get("active", True):
            raise RuntimeError(f"{name} missing active market {symbol}")
        markets[asset] = fetch_ohlcv_range(swap, symbol, "1h", warmup, end)
        funding[asset] = fetch_funding_range(swap, symbol, funding_start, end)
    if "BTC/USDT" not in spot.markets or not spot.markets["BTC/USDT"].get("active", True):
        raise RuntimeError(f"{name} missing active BTC/USDT spot")
    btc_daily = fetch_ohlcv_range(spot, "BTC/USDT", "1d", warmup, end)
    return name, {
        "swap_exchange": contract["swap"], "spot_exchange": contract["spot"],
        "markets_1h": markets, "funding": funding, "btc_spot_1d": btc_daily,
    }


def market_frame(rows: list[list[float]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=("ts", "open", "high", "low", "close", "volume"))
    frame["ts"] = pd.to_datetime(frame.ts, unit="ms", utc=True)
    return frame.set_index("ts")


def prepare(raw: dict) -> dict[str, pd.DataFrame]:
    contract = funding_crowding.FROZEN_CONTRACT.trend_contract()
    result = {}
    for asset in ASSETS:
        frame = trend_sentiment.add_features(
            market_frame(raw["markets_1h"][asset]), None, contract
        )
        frame["trailing_dollar_volume"] = (
            (frame.close * frame.volume).shift(1).rolling(
                24 * 30, min_periods=24 * 7
            ).mean()
        )
        rates = pd.DataFrame(raw["funding"][asset], columns=("ts", "funding_rate"))
        rates["ts"] = pd.to_datetime(rates.ts, unit="ms", utc=True)
        rates = rates.set_index("ts").funding_rate.sort_index()
        mean = rates.shift(1).rolling(21, min_periods=7).mean()
        std = rates.shift(1).rolling(21, min_periods=7).std().replace(0, np.nan)
        funding_features = pd.DataFrame({
            "cumulative_funding": rates.cumsum(), "funding_z": (rates - mean) / std,
        })
        left = frame.reset_index().rename(columns={frame.index.name or "index": "ts"})
        right = funding_features.reset_index().rename(
            columns={funding_features.index.name or "index": "ts"}
        )
        frame = pd.merge_asof(left.sort_values("ts"), right.sort_values("ts"),
                              on="ts", direction="backward").set_index("ts")
        frame["cumulative_funding"] = frame.cumulative_funding.ffill().fillna(0.0)
        result[asset] = frame
    return result


def btc_returns(rows: list[list[float]], start: pd.Timestamp, end: pd.Timestamp,
                cost_pct: float) -> pd.Series:
    daily = market_frame(rows)
    featured = btc_spot_trend.add_features(daily)
    index = daily.index[(daily.index >= start) & (daily.index < end)]
    desired = featured.target_exposure.shift(1).reindex(index).fillna(0.0)
    opens = daily.open.reindex(index)
    prior_exposure, prior_open = 0.0, None
    values = []
    for timestamp in index:
        current_open = float(opens.loc[timestamp])
        gross = prior_exposure * (current_open / prior_open - 1) if prior_open else 0.0
        target = float(desired.loc[timestamp])
        values.append(gross - abs(target - prior_exposure) * cost_pct / 100)
        prior_exposure, prior_open = target, current_open
    if prior_exposure and values:
        values[-1] -= prior_exposure * cost_pct / 100
    return pd.Series(values, index=index, dtype=float)


def trade_daily(trades: list[dict], start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    dates = pd.date_range(start, end - pd.Timedelta(days=1), freq="1D")
    if not trades:
        return pd.Series(0.0, index=dates)
    frame = pd.DataFrame(trades)
    frame["exit_time"] = pd.to_datetime(frame.exit_time, utc=True)
    values = frame.set_index("exit_time").net_equity_return.resample("1D").apply(
        lambda part: np.prod(1 + part) - 1
    )
    return values.reindex(dates, fill_value=0.0)


def serialize_trade(trade: dict) -> dict:
    return {key: value.isoformat() if isinstance(value, pd.Timestamp)
            else float(value) if isinstance(value, np.floating) else value
            for key, value in trade.items()}


def evaluate(name: str, raw: dict, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    featured = prepare(raw)
    contract = funding_crowding.FROZEN_CONTRACT.trend_contract()
    scenarios = {}
    scenario_trades = {}
    for scenario in ("base", "stress"):
        trades = simulate(
            featured, contract, start, end,
            funding_crowding.FROZEN_CONTRACT.top_n_liquid,
            funding_crowding.FROZEN_CONTRACT.max_concurrent,
            0.0, FAST_COST[scenario],
            funding_crowding.FROZEN_CONTRACT.funding_crowding_z,
            close_at_end=True,
        )
        fast = trade_daily(trades, start, end)
        btc = btc_returns(raw["btc_spot_1d"], start, end, BTC_COST[scenario])
        composite = composite_returns(btc, fast, .5)
        episodes = len({pd.Timestamp(trade["signal_time"]) for trade in trades})
        weeks = (end - start) / pd.Timedelta(days=7)
        scenarios[scenario] = {
            "composite": portfolio_metrics(composite),
            "btc_sleeve": portfolio_metrics(btc),
            "funding_sleeve": portfolio_metrics(fast),
            "positions": len(trades), "independent_risk_episodes": episodes,
            "episodes_per_week": episodes / weeks,
            "sides": dict(Counter(trade["side"] for trade in trades)),
            "exit_reasons": dict(Counter(trade["exit_reason"] for trade in trades)),
        }
        scenario_trades[scenario] = [serialize_trade(trade) for trade in trades]
    # Behavior is cost-independent; keep one trade ledger and assert it.
    base_keys = [(row["symbol"], row["side"], row["entry_time"], row["exit_time"])
                 for row in scenario_trades["base"]]
    stress_keys = [(row["symbol"], row["side"], row["entry_time"], row["exit_time"])
                   for row in scenario_trades["stress"]]
    if base_keys != stress_keys:
        raise AssertionError(f"{name} base/stress trade behavior diverged")
    closed_reference = [trade for trade in scenario_trades["base"]
                        if trade["exit_reason"] != "WINDOW_END_MARK"]
    production = production_replay(featured, start, end)
    mismatches = parity_compare(closed_reference, production)
    return {
        "runtime_parity": {"passed": not mismatches,
                           "reference_closed_trades": len(closed_reference),
                           "production_trades": len(production),
                           "mismatches": mismatches},
        "scenarios": scenarios, "trades": scenario_trades["base"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reuse-cache", action="store_true")
    args = parser.parse_args()
    if args.reuse_cache:
        with gzip.open(CACHE, "rt", encoding="utf-8") as handle:
            cache = json.load(handle)
        fetched_at = pd.Timestamp(cache["metadata"]["fetched_at"])
        start, end = pd.Timestamp(cache["metadata"]["start"]), pd.Timestamp(cache["metadata"]["end"])
        venues = cache["venues"]
    else:
        fetched_at = pd.Timestamp.now(tz="UTC")
        end = fetched_at.normalize()
        start = end - pd.Timedelta(days=30)
        warmup = start - pd.Timedelta(days=70)
        funding_start = start - pd.Timedelta(days=10)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(fetch_venue, name, contract, warmup, funding_start, end)
                       for name, contract in VENUES.items()]
            venues = dict(future.result() for future in futures)
        cache = {
            "metadata": {"fetched_at": fetched_at.isoformat(), "start": start.isoformat(),
                         "end": end.isoformat(), "warmup_start": warmup.isoformat(),
                         "funding_start": funding_start.isoformat(), "ccxt": ccxt.__version__,
                         "window": "last 30 fully closed UTC days", "start_state": "flat"},
            "venues": venues,
        }
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(CACHE, "wt", encoding="utf-8") as handle:
            json.dump(cache, handle, separators=(",", ":"))
    results = {name: evaluate(name, raw, start, end) for name, raw in venues.items()}
    output = {
        "package_id": funding_crowding.COMPOSITE_PACKAGE_ID,
        "status": "RECENT_30D_DIAGNOSTIC_NOT_PROMOTION_EVIDENCE",
        "live_execution": False,
        "window": {"start": start.isoformat(), "end": end.isoformat(),
                   "days": 30, "start_state": "flat",
                   "end_positions": "marked at final close with exit cost"},
        "contract": {"allocation": {"btc": .5, "funding": .5},
                     "fast_round_trip_cost_pct": FAST_COST,
                     "btc_turnover_cost_pct": BTC_COST,
                     "universe": list(ASSETS)},
        "data": {"cache": str(CACHE), "sha256": hashlib.sha256(CACHE.read_bytes()).hexdigest(),
                 "source": "public venue REST APIs", "fetched_at": fetched_at.isoformat(),
                 "coverage": {name: {
                     "assets": len(raw["markets_1h"]),
                     "minimum_1h_rows": min(len(rows) for rows in raw["markets_1h"].values()),
                     "minimum_funding_rows": min(len(rows) for rows in raw["funding"].values()),
                     "btc_daily_rows": len(raw["btc_spot_1d"]),
                 } for name, raw in venues.items()}},
        "results": results,
    }
    OUTPUT.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps({"window": output["window"], "data": output["data"],
                      "results": {name: value["scenarios"]
                                  for name, value in results.items()}}, indent=2))


if __name__ == "__main__":
    main()
