"""Accelerated SQLite Paper lifecycle for the frozen funding-crowding sleeve."""
import json

import numpy as np
import pandas as pd

from .. import config, state_store
from ..engine import funding_crowding as strategy


def _iso(value) -> str:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.isoformat()


def run_accelerated_replay(trades: list[dict], initial_equity_usd: float | None = None) -> dict:
    ordered = sorted(trades, key=lambda item: pd.Timestamp(item["exit_time"]))
    initial_equity = float(initial_equity_usd or config.ACCOUNT_EQUITY_USD)
    equity, active, returns = initial_equity, {}, []
    counters = {"entries": 0, "exits": 0, "max_concurrent": 0}
    events = []
    for trade in ordered:
        events.append((pd.Timestamp(trade["entry_time"]), 1, "ENTRY", trade))
        events.append((pd.Timestamp(trade["exit_time"]), 0, "EXIT", trade))
    events.sort(key=lambda item: (item[0], item[1], item[3]["symbol"]))

    with state_store.session() as conn:
        existing = conn.execute(
            "SELECT (SELECT COUNT(*) FROM event_log), (SELECT COUNT(*) FROM position_state), "
            "(SELECT COUNT(*) FROM equity_ledger)"
        ).fetchone()
        if any(existing):
            raise RuntimeError("accelerated replay requires an empty database")
        state_store.log_event("REPLAY_STARTED", {
            "strategy_package_id": strategy.PACKAGE_ID,
            "contract": strategy.FROZEN_CONTRACT.manifest(), "clock": "simulated",
        }, ts=_iso(events[0][0]))

        for timestamp, _, kind, trade in events:
            key = (trade["symbol"], _iso(trade["entry_time"]))
            ts = _iso(timestamp)
            if kind == "ENTRY":
                features = {
                    "stop_atr_multiple": trade["stop_atr_multiple"],
                    "funding_z": trade["signal_funding_z"],
                    "stop_price": trade["stop_price"],
                    "take_profit_price": trade["take_profit_price"],
                    "capital_fraction": trade["capital_fraction"],
                }
                lineage = {
                    "strategy_package_id": strategy.PACKAGE_ID,
                    "source": "historical_binance_um_1h_and_funding",
                    "clock": "simulated", "execution_policy": "next_hour_open",
                }
                state_store.log_feature_snapshot(
                    trade["symbol"], trade["entry_price"], features, lineage, ts=ts
                )
                state_store.log_signal(
                    trade["symbol"], trade["entry_price"], trade["side"],
                    trade["stop_atr_multiple"], {"stop_atr_multiple": trade["stop_atr_multiple"],
                                              "funding_z": trade["signal_funding_z"]},
                    notes="FUNDING_CROWDING_NEXT_HOUR_OPEN", ts=ts,
                )
                trade_id = state_store.open_position(
                    trade["symbol"], trade["entry_price"], ts, trade["stop_atr_multiple"],
                    trade["stop_price"], trade["take_profit_price"],
                    trade["capital_fraction"] * equity,
                    tp_reason="ADAPTIVE_R_MULTIPLE", scoring_profile="funding_crowding",
                    position_meta={"side": trade["side"],
                                   "strategy_package_id": strategy.PACKAGE_ID,
                                   "signal_time": trade["signal_time"]},
                )
                active[key] = trade_id
                state_store.log_event("ENTRY", {
                    "side": trade["side"], "price": trade["entry_price"],
                    "equity_usd": equity, "capital_fraction": trade["capital_fraction"],
                }, trade_id=trade_id, ts=ts)
                counters["entries"] += 1
                counters["max_concurrent"] = max(counters["max_concurrent"], len(active))
            else:
                trade_id = active.pop(key)
                net_return = float(trade["net_equity_return"])
                pnl_usd = equity * net_return
                accounting = {
                    "net_pnl_usd": pnl_usd, "net_equity_return": net_return,
                    "funding_return": trade["funding_return"],
                    "fees_included": True, "round_trip_cost_pct": .07,
                }
                recorded = state_store.record_trade_accounting(trade_id, accounting, ts=ts)
                equity = float(recorded["equity_after_usd"])
                returns.append(net_return)
                state_store.log_event("EXIT", {
                    "side": trade["side"], "price": trade["exit_price"],
                    "reason": trade["exit_reason"], "equity_usd": equity,
                    "accounting": accounting,
                }, trade_id=trade_id, ts=ts)
                state_store.close_position(trade_id)
                counters["exits"] += 1
        state_store.set_kv("funding_crowding_replay", json.dumps({
            "equity_usd": equity, "counters": counters,
            "last_ts": _iso(events[-1][0]),
        }, sort_keys=True))
        state_store.log_event("REPLAY_COMPLETED", {
            "strategy_package_id": strategy.PACKAGE_ID,
            "equity_usd": equity, "counters": counters,
        }, ts=_iso(events[-1][0] + pd.Timedelta(microseconds=1)))
    values = np.asarray(returns)
    curve = initial_equity * np.cumprod(1 + values)
    path = np.r_[initial_equity, curve]
    drawdown = path / np.maximum.accumulate(path) - 1
    return {
        "initial_equity_usd": initial_equity, "final_equity_usd": equity,
        "net_return_pct": (equity / initial_equity - 1) * 100,
        "max_drawdown_pct": float(-drawdown.min() * 100),
        "returns": values, "counters": counters,
    }
