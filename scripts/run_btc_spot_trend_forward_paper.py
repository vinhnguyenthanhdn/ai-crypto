"""One-shot BTC Spot trend forward Paper observer; public data, no orders."""
import argparse
import json
from pathlib import Path

import pandas as pd
import requests

from src import config, state_store
from src.engine import btc_spot_trend as strategy


KLINES_URL = "https://api.binance.com/api/v3/klines"
BASE_COST_PCT = 0.12
STRESS_COST_PCT = 0.24
STATE_KEY = "btc_spot_trend_forward_state"
SYMBOL = "BTC/USDT"


def advance_portfolio(prior_exposure: float, prior_open: float, current_open: float,
                      target_exposure: float, base_equity: float,
                      stress_equity: float) -> dict:
    gross_return = (
        prior_exposure * (current_open / prior_open - 1) if prior_open else 0.0
    )
    turnover = abs(target_exposure - prior_exposure)
    base_return = gross_return - turnover * BASE_COST_PCT / 100
    stress_return = gross_return - turnover * STRESS_COST_PCT / 100
    return {
        "gross_return": gross_return,
        "turnover": turnover,
        "base_return": base_return,
        "stress_return": stress_return,
        "base_equity_usd": base_equity * (1 + base_return),
        "stress_equity_usd": stress_equity * (1 + stress_return),
    }


def fetch_daily(now_ms: int) -> tuple[pd.DataFrame, pd.Series]:
    response = requests.get(KLINES_URL, params={
        "symbol": "BTCUSDT", "interval": "1d", "limit": 200,
    }, timeout=30)
    response.raise_for_status()
    rows = response.json()
    frame = pd.DataFrame([{
        "ts": int(row[0]), "open": float(row[1]), "high": float(row[2]),
        "low": float(row[3]), "close": float(row[4]), "volume": float(row[5]),
        "close_time": int(row[6]),
    } for row in rows])
    opens = pd.Series(
        frame.open.to_numpy(),
        index=pd.to_datetime(frame.ts.to_numpy(), unit="ms", utc=True),
        dtype=float,
    )
    closed = frame[frame.close_time < now_ms].copy()
    closed["ts"] = pd.to_datetime(closed.ts, unit="ms", utc=True)
    return closed.set_index("ts").drop(columns="close_time"), opens


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/state_btc_spot_trend_forward.db")
    parser.add_argument("--status", default="data/backtests/btc_spot_trend_forward_status.json")
    parser.add_argument("--initial-equity", type=float, default=250.0)
    args = parser.parse_args()
    db_path = Path(args.db)
    if db_path.resolve() == Path(config.DB_PATH).resolve():
        raise RuntimeError("forward Paper requires a database separate from runtime")
    config.DB_PATH = db_path
    config.ACCOUNT_EQUITY_USD = args.initial_equity

    now = pd.Timestamp.now(tz="UTC")
    daily, entry_opens = fetch_daily(int(now.timestamp() * 1000))
    featured = strategy.add_features(daily)
    signal_ts = featured.index[-1]
    entry_ts = signal_ts + pd.Timedelta(days=1)
    if entry_ts not in entry_opens.index:
        raise RuntimeError(f"missing exact next-day open for {entry_ts}")
    entry_open = float(entry_opens.loc[entry_ts])
    decision = strategy.decision_at(featured)

    bootstrap = False
    processed = False
    with state_store.session() as conn:
        raw_state = state_store.get_kv(STATE_KEY)
        if raw_state is None:
            # The first mid-day observation cannot claim a fill at an open that
            # happened before the observer existed. Start flat and act next day.
            bootstrap = True
            portfolio = {
                "base_equity_usd": args.initial_equity,
                "stress_equity_usd": args.initial_equity,
                "target_exposure": 0.0,
                "prior_open": entry_open,
                "active_trade_id": None,
                "last_signal_ts": signal_ts.isoformat(),
            }
            state_store.log_event("PAPER_OBSERVATION", {
                "mode": "PAPER_NO_ORDER", "bootstrap": True,
                "signal_ts": signal_ts.isoformat(), "executed_target": 0.0,
                "next_target_observed": decision["target_exposure"],
            }, ts=now.isoformat())
            state_store.set_kv(STATE_KEY, json.dumps(portfolio, sort_keys=True))
        else:
            portfolio = json.loads(raw_state)
            if portfolio["last_signal_ts"] != signal_ts.isoformat():
                processed = True
                prior_exposure = float(portfolio["target_exposure"])
                target = float(decision["target_exposure"])
                change = advance_portfolio(
                    prior_exposure, float(portfolio["prior_open"]), entry_open, target,
                    float(portfolio["base_equity_usd"]),
                    float(portfolio["stress_equity_usd"]),
                )
                base_equity = change["base_equity_usd"]
                active_trade_id = portfolio.get("active_trade_id")
                ts = entry_ts.isoformat()
                features = {
                    "close": decision["close"], "trend_sma": decision["trend_sma"],
                    "realized_volatility": decision["realized_volatility"],
                    "target_exposure": target, "executed_open": entry_open,
                }
                state_store.log_feature_snapshot(SYMBOL, entry_open, features, {
                    "strategy_package_id": strategy.PACKAGE_ID,
                    "source": "Binance Spot public closed 1d kline",
                    "mode": "PAPER_NO_ORDER", "execution_policy": "next_daily_open",
                }, ts=ts)
                state_store.log_signal(
                    SYMBOL, entry_open, decision["action"], target * 100,
                    {"btc_spot_trend": target * 100}, notes="PAPER_NO_ORDER", ts=ts,
                )
                if prior_exposure == 0 and target > 0:
                    active_trade_id = state_store.open_position(
                        SYMBOL, entry_open, ts, target * 100, None, None,
                        target * base_equity, tp_reason="TREND_OFF",
                        scoring_profile="btc_spot_trend_forward",
                        position_meta={"target_exposure": target,
                                       "strategy_package_id": strategy.PACKAGE_ID,
                                       "mode": "PAPER_NO_ORDER"},
                    )
                    state_store.log_event("ENTRY", {
                        "price": entry_open, "target_exposure": target,
                        "base_equity_usd": base_equity, "mode": "PAPER_NO_ORDER",
                    }, trade_id=active_trade_id, ts=ts)
                elif prior_exposure > 0 and target == 0:
                    ledger_equity = state_store.get_current_equity_usd()
                    accounting = {
                        "net_pnl_usd": base_equity - ledger_equity,
                        "base_equity_usd": base_equity,
                        "stress_equity_usd": change["stress_equity_usd"],
                        "fees_included": True, "base_cost_pct": BASE_COST_PCT,
                        "stress_cost_pct": STRESS_COST_PCT,
                    }
                    state_store.record_trade_accounting(active_trade_id, accounting, ts=ts)
                    state_store.log_event("EXIT", {
                        "price": entry_open, "reason": "TREND_OFF",
                        "accounting": accounting, "mode": "PAPER_NO_ORDER",
                    }, trade_id=active_trade_id, ts=ts)
                    state_store.close_position(active_trade_id)
                    active_trade_id = None
                elif target > 0 and target != prior_exposure:
                    state_store.resize_position(active_trade_id, target * base_equity, {
                        "target_exposure": target,
                        "strategy_package_id": strategy.PACKAGE_ID,
                        "mode": "PAPER_NO_ORDER",
                    })
                    state_store.log_event("REBALANCE", {
                        "price": entry_open, "from_exposure": prior_exposure,
                        "to_exposure": target, "turnover": change["turnover"],
                        "base_equity_usd": base_equity, "mode": "PAPER_NO_ORDER",
                    }, trade_id=active_trade_id, ts=ts)
                state_store.log_event("PAPER_OBSERVATION", {
                    "mode": "PAPER_NO_ORDER", "bootstrap": False,
                    "signal_ts": signal_ts.isoformat(), "executed_target": target,
                    **change,
                }, ts=now.isoformat())
                portfolio = {
                    "base_equity_usd": base_equity,
                    "stress_equity_usd": change["stress_equity_usd"],
                    "target_exposure": target, "prior_open": entry_open,
                    "active_trade_id": active_trade_id,
                    "last_signal_ts": signal_ts.isoformat(),
                }
                state_store.set_kv(STATE_KEY, json.dumps(portfolio, sort_keys=True))

        counts = {
            "observed_days": conn.execute(
                "SELECT COUNT(*) FROM event_log WHERE type='PAPER_OBSERVATION'"
            ).fetchone()[0],
            "closed_trades": conn.execute("SELECT COUNT(*) FROM equity_ledger").fetchone()[0],
            "open_positions": conn.execute(
                "SELECT COUNT(*) FROM position_state WHERE status='IN_POSITION'"
            ).fetchone()[0],
        }

    output = {
        "strategy_package_id": strategy.PACKAGE_ID,
        "mode": "FRESH_FORWARD_PAPER_NO_ORDER", "live_execution": False,
        "observed_at": now.isoformat(), "signal_ts": signal_ts.isoformat(),
        "next_daily_open_ts": entry_ts.isoformat(), "next_daily_open": entry_open,
        "decision": decision, "bootstrap": bootstrap, "processed": processed,
        "base_equity_usd": portfolio["base_equity_usd"],
        "stress_equity_usd": portfolio["stress_equity_usd"],
        "executed_target_exposure": portfolio["target_exposure"],
        "progress": counts,
    }
    status_path = Path(args.status)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
