"""One-shot forward Paper observer; fetches public data and never sends orders."""
import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from src import config, state_store
from src.engine import funding_crowding as strategy
from src.engine import trend_sentiment


KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
ROUND_TRIP_COST_PCT = .07


def fetch_symbol(symbol: str, now_ms: int) -> tuple[str, pd.DataFrame, pd.Series, pd.Series]:
    klines = requests.get(KLINES_URL, params={
        "symbol": symbol, "interval": "1h", "limit": 1500,
    }, timeout=30)
    klines.raise_for_status()
    rows = klines.json()
    frame = pd.DataFrame([{  # keep close_time to exclude the forming candle.
        "ts": int(row[0]), "open": float(row[1]), "high": float(row[2]),
        "low": float(row[3]), "close": float(row[4]), "volume": float(row[5]),
        "close_time": int(row[6]),
    } for row in rows])
    entry_opens = pd.Series(
        frame.open.to_numpy(),
        index=pd.to_datetime(frame.ts.to_numpy(), unit="ms", utc=True),
        dtype=float,
    )
    closed = frame[frame.close_time < now_ms].copy()
    closed["ts"] = pd.to_datetime(closed.ts, unit="ms", utc=True)
    closed = closed.set_index("ts").drop(columns="close_time")

    response = requests.get(FUNDING_URL, params={"symbol": symbol, "limit": 100}, timeout=30)
    response.raise_for_status()
    funding_rows = response.json()
    funding = pd.Series(
        [float(row["fundingRate"]) for row in funding_rows],
        index=pd.to_datetime([int(row["fundingTime"]) for row in funding_rows], unit="ms", utc=True),
        dtype=float,
    ).sort_index()
    return symbol, closed, funding, entry_opens


def attach_features(market: pd.DataFrame, funding: pd.Series) -> pd.DataFrame:
    frame = trend_sentiment.add_features(
        market, None, strategy.FROZEN_CONTRACT.trend_contract()
    )
    frame["trailing_dollar_volume"] = (
        (frame.close * frame.volume).shift(1).rolling(24 * 30, min_periods=24 * 7).mean()
    )
    prior_mean = funding.shift(1).rolling(21, min_periods=7).mean()
    prior_std = funding.shift(1).rolling(21, min_periods=7).std().replace(0, np.nan)
    funding_z = ((funding - prior_mean) / prior_std).rename("funding_z").to_frame()
    left = frame.reset_index().rename(columns={frame.index.name or "index": "ts"})
    right = funding_z.reset_index().rename(columns={funding_z.index.name or "index": "ts"})
    return pd.merge_asof(left.sort_values("ts"), right.sort_values("ts"),
                         on="ts", direction="backward").set_index("ts")


def realized_funding(funding: pd.Series, entry_time, exit_time, side: str) -> float:
    paid = float(funding[(funding.index > pd.Timestamp(entry_time))
                         & (funding.index <= pd.Timestamp(exit_time))].sum())
    return -paid if side == "LONG" else paid


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/state_funding_crowding_forward.db")
    parser.add_argument("--status", default="data/backtests/funding_crowding_forward_status.json")
    parser.add_argument("--initial-equity", type=float, default=250.0)
    args = parser.parse_args()
    db_path = Path(args.db)
    if db_path.resolve() == Path(config.DB_PATH).resolve():
        raise RuntimeError("forward Paper requires a database separate from runtime")
    config.DB_PATH = db_path
    config.ACCOUNT_EQUITY_USD = args.initial_equity
    reference = json.loads(Path(
        "data/backtests/multiasset_funding_crowding_5y.json"
    ).read_text())
    symbols = reference["universe"]
    now = pd.Timestamp.now(tz="UTC")
    now_ms = int(now.timestamp() * 1000)
    with ThreadPoolExecutor(max_workers=8) as pool:
        fetched = list(pool.map(lambda symbol: fetch_symbol(symbol, now_ms), symbols))
    markets, funding, entry_opens = {}, {}, {}
    for symbol, market, rates, opens in fetched:
        markets[symbol], funding[symbol], entry_opens[symbol] = market, rates, opens
    featured = {symbol: attach_features(markets[symbol], funding[symbol]) for symbol in symbols}
    signal_ts = min(frame.index[-1] for frame in featured.values())
    entry_ts = signal_ts + pd.Timedelta(hours=1)
    rows = {symbol: frame.loc[signal_ts] for symbol, frame in featured.items()
            if signal_ts in frame.index and entry_ts in entry_opens[symbol].index}
    if set(rows) != set(symbols):
        missing = sorted(set(symbols) - set(rows))
        raise RuntimeError(f"incomplete common-hour universe: missing {missing}")
    exits, entries = [], []

    with state_store.session() as conn:
        cooldown_raw = json.loads(state_store.get_kv("funding_crowding_cooldown", "{}"))
        if state_store.get_kv("funding_crowding_last_input_ts") != signal_ts.isoformat():
            for symbol, row in rows.items():
                numeric = (row.trend_direction, row.trend_strength, row.entry_atr,
                           row.funding_z, row.trailing_dollar_volume)
                if not all(np.isfinite(float(value)) for value in numeric):
                    raise RuntimeError(f"non-finite hourly input for {symbol}")
                snapshot = {
                    "trend_direction": float(row.trend_direction),
                    "trend_strength": float(row.trend_strength),
                    "long_signal": bool(row.long_signal),
                    "short_signal": bool(row.short_signal),
                    "entry_atr": float(row.entry_atr),
                    "funding_z": float(row.funding_z),
                    "trailing_dollar_volume": float(row.trailing_dollar_volume),
                    "signal_ts": signal_ts.isoformat(),
                }
                state_store.log_feature_snapshot(
                    symbol, float(entry_opens[symbol].loc[entry_ts]), snapshot,
                    {"strategy_package_id": strategy.PACKAGE_ID,
                     "source": "Binance UM public 1h/funding",
                     "mode": "PAPER_NO_ORDER", "snapshot_type": "hourly_input",
                     "execution_policy": "next_hour_open"},
                    ts=entry_ts.isoformat(),
                )
            state_store.log_event("PAPER_INPUT_SNAPSHOT", {
                "signal_ts": signal_ts.isoformat(), "symbols": sorted(rows),
                "feature_rows": len(rows), "mode": "PAPER_NO_ORDER",
            }, ts=now.isoformat())
            state_store.set_kv("funding_crowding_last_input_ts", signal_ts.isoformat())
        positions = state_store.get_open_positions()
        for position in positions:
            symbol = position["symbol"]
            row = rows.get(symbol)
            if row is None or signal_ts < pd.Timestamp(position["entry_time"]):
                continue
            meta = position["position_meta"]
            core_position = {
                "side": meta["side"], "stop_price": position["stop_price"],
                "take_profit_price": position["take_profit_price"],
                "entry_time": pd.Timestamp(position["entry_time"]),
            }
            decision = strategy.exit_decision(core_position, row, signal_ts)
            if decision is None:
                continue
            exit_price, reason = decision
            direction = 1 if meta["side"] == "LONG" else -1
            price_return = direction * (exit_price / position["entry_price"] - 1)
            funding_return = realized_funding(
                funding[symbol], position["entry_time"], signal_ts, meta["side"]
            )
            account_return = meta["capital_fraction"] * (
                price_return + funding_return - ROUND_TRIP_COST_PCT / 100
            )
            equity = state_store.get_current_equity_usd()
            accounting = {
                "net_pnl_usd": equity * account_return,
                "net_equity_return": account_return,
                "funding_return": funding_return, "fees_included": True,
                "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
            }
            state_store.record_trade_accounting(position["trade_id"], accounting, ts=signal_ts.isoformat())
            state_store.log_event("EXIT", {
                "side": meta["side"], "price": exit_price, "reason": reason,
                "accounting": accounting, "mode": "PAPER_NO_ORDER",
            }, trade_id=position["trade_id"], ts=signal_ts.isoformat())
            state_store.close_position(position["trade_id"])
            cooldown_raw[symbol] = (
                signal_ts + pd.Timedelta(hours=strategy.FROZEN_CONTRACT.cooldown_hours)
            ).isoformat()
            exits.append({"symbol": symbol, "side": meta["side"], "reason": reason,
                          "price": exit_price, "return": account_return})

        last_signal = state_store.get_kv("funding_crowding_last_signal_ts")
        if last_signal != signal_ts.isoformat():
            positions = state_store.get_open_positions()
            blocked = {position["symbol"] for position in positions}
            blocked |= {symbol for symbol, until in cooldown_raw.items()
                        if signal_ts < pd.Timestamp(until)}
            slots = strategy.FROZEN_CONTRACT.max_concurrent - len(positions)
            decisions = strategy.rank_entries(rows, blocked, slots)
            for decision in decisions:
                symbol = decision["symbol"]
                # Bind the fill to the exact bar after the common signal hour;
                # never use an unrelated latest open if requests cross an hour boundary.
                entry_price = float(entry_opens[symbol].loc[entry_ts])
                plan = strategy.entry_plan(entry_price, decision)
                equity = state_store.get_current_equity_usd()
                entry_time = entry_ts
                features = {**decision, **plan, "signal_ts": signal_ts.isoformat()}
                lineage = {
                    "strategy_package_id": strategy.PACKAGE_ID,
                    "source": "Binance UM public 1h/funding",
                    "mode": "PAPER_NO_ORDER", "execution_policy": "next_hour_open",
                }
                state_store.log_feature_snapshot(
                    symbol, entry_price, features, lineage, ts=entry_time.isoformat()
                )
                state_store.log_signal(
                    symbol, entry_price, decision["side"], decision["trend_strength"],
                    {"trend_strength": decision["trend_strength"],
                     "funding_z": decision["funding_z"]},
                    notes="PAPER_NO_ORDER", ts=entry_time.isoformat(),
                )
                trade_id = state_store.open_position(
                    symbol, entry_price, entry_time.isoformat(), decision["trend_strength"],
                    plan["stop_price"], plan["take_profit_price"],
                    plan["capital_fraction"] * equity,
                    tp_reason="ADAPTIVE_R_MULTIPLE", scoring_profile="funding_crowding_forward",
                    position_meta={
                        "side": decision["side"],
                        "capital_fraction": plan["capital_fraction"],
                        "signal_time": signal_ts.isoformat(),
                        "strategy_package_id": strategy.PACKAGE_ID,
                        "mode": "PAPER_NO_ORDER",
                    },
                )
                state_store.log_event("ENTRY", {
                    "side": decision["side"], "price": entry_price,
                    "stop_price": plan["stop_price"],
                    "take_profit_price": plan["take_profit_price"],
                    "capital_fraction": plan["capital_fraction"],
                    "mode": "PAPER_NO_ORDER",
                }, trade_id=trade_id, ts=entry_time.isoformat())
                entries.append({"symbol": symbol, "side": decision["side"],
                                "price": entry_price, **plan})
            state_store.set_kv("funding_crowding_last_signal_ts", signal_ts.isoformat())
        state_store.set_kv("funding_crowding_cooldown", json.dumps(cooldown_raw, sort_keys=True))
        open_positions = state_store.get_open_positions()
        equity = state_store.get_current_equity_usd()
        if state_store.get_kv("funding_crowding_last_observation_ts") != signal_ts.isoformat():
            state_store.log_event("PAPER_OBSERVATION", {
                "signal_ts": signal_ts.isoformat(), "entries": len(entries),
                "exits": len(exits), "open_positions": len(open_positions),
                "mode": "PAPER_NO_ORDER",
            }, ts=now.isoformat())
            state_store.set_kv("funding_crowding_last_observation_ts", signal_ts.isoformat())
        progress = {
            "observed_hours": conn.execute(
                "SELECT COUNT(*) FROM event_log WHERE type='PAPER_OBSERVATION'"
            ).fetchone()[0],
            "closed_trades": conn.execute("SELECT COUNT(*) FROM equity_ledger").fetchone()[0],
            "independent_risk_episodes": conn.execute(
                "SELECT COUNT(DISTINCT ts) FROM event_log WHERE type='ENTRY'"
            ).fetchone()[0],
            "input_snapshots": conn.execute(
                "SELECT COUNT(*) FROM event_log WHERE type='PAPER_INPUT_SNAPSHOT'"
            ).fetchone()[0],
            "hourly_input_features": sum(
                1 for (lineage,) in conn.execute("SELECT lineage FROM feature_snapshot")
                if json.loads(lineage).get("snapshot_type") == "hourly_input"
            ),
        }

    output = {
        "strategy_package_id": strategy.COMPOSITE_PACKAGE_ID,
        "sleeve_package_id": strategy.PACKAGE_ID,
        "mode": "FRESH_FORWARD_PAPER_NO_ORDER", "live_execution": False,
        "observed_at": now.isoformat(), "signal_ts": signal_ts.isoformat(),
        "universe": symbols, "entries": entries, "exits": exits,
        "open_positions": open_positions, "fast_sleeve_equity_usd": equity,
        "forward_progress": progress,
        "required_promotion_sample": {"closed_trades": 30,
                                      "independent_risk_episodes": 30},
    }
    status_path = Path(args.status)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
