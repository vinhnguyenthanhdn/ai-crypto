"""Accelerated SQLite Paper lifecycle for the frozen BTC Spot trend strategy."""
import json

import numpy as np
import pandas as pd

from .. import config, state_store
from ..engine import btc_spot_trend as strategy


STATE_KEY = "btc_spot_trend_portfolio_state"


def _iso(value):
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.isoformat()


def run_accelerated_replay(featured, start, end, *, cost_pct=0.12,
                           symbol="BTC/USDT", initial_equity_usd=None):
    frame = featured[(featured.index >= pd.Timestamp(start)) &
                     (featured.index < pd.Timestamp(end))].copy()
    if frame.empty:
        raise ValueError("không có daily bar trong khoảng replay")
    desired = featured.target_exposure.shift(1).reindex(frame.index).fillna(0.0)
    initial_equity = float(initial_equity_usd or config.ACCOUNT_EQUITY_USD)
    equity = initial_equity
    prior_exposure = 0.0
    prior_open = None
    active_trade_id = None
    daily_returns = []
    counters = {"days": 0, "entries": 0, "rebalances": 0, "exits": 0}

    with state_store.session() as conn:
        existing = conn.execute(
            "SELECT (SELECT COUNT(*) FROM event_log), (SELECT COUNT(*) FROM position_state), "
            "(SELECT COUNT(*) FROM equity_ledger)"
        ).fetchone()
        if any(existing):
            raise RuntimeError("accelerated replay yêu cầu DB rỗng")
        state_store.log_event("REPLAY_STARTED", {
            "start": str(start), "end": str(end), "clock": "simulated",
            "contract": strategy.FROZEN_CONTRACT.manifest(),
            "strategy_package_id": strategy.PACKAGE_ID,
        }, ts=_iso(frame.index[0]))

        for timestamp, row in frame.iterrows():
            counters["days"] += 1
            target = float(desired.loc[timestamp])
            gross = prior_exposure * (float(row.open) / prior_open - 1) if prior_open else 0.0
            turnover = abs(target - prior_exposure)
            net_return = gross - turnover * cost_pct / 100
            equity *= 1 + net_return
            daily_returns.append(net_return)
            ts = _iso(timestamp)
            features = {
                "close": float(row.close),
                "trend_sma": None if pd.isna(row.trend_sma) else float(row.trend_sma),
                "realized_volatility": None if pd.isna(row.realized_volatility) else float(row.realized_volatility),
                "target_exposure_next_open": float(row.target_exposure),
                "executed_exposure": target,
            }
            state_store.log_feature_snapshot(symbol, float(row.open), features, {
                "strategy_package_id": strategy.PACKAGE_ID,
                "source": "historical_binance_spot_5m_to_closed_1d",
                "clock": "simulated",
            }, ts=ts)
            state_store.log_signal(symbol, float(row.open), "LONG" if target else "CASH",
                                   target * 100, {"btc_spot_trend": target * 100},
                                   notes="NEXT_DAILY_OPEN", ts=ts)

            if prior_exposure == 0 and target > 0:
                active_trade_id = state_store.open_position(
                    symbol, float(row.open), ts, target * 100, None, None,
                    target * equity, tp_reason="TREND_OFF", scoring_profile="btc_spot_trend",
                    position_meta={"target_exposure": target, "strategy_package_id": strategy.PACKAGE_ID},
                )
                state_store.log_event("ENTRY", {"price": float(row.open), "target_exposure": target,
                                      "equity_usd": equity}, trade_id=active_trade_id, ts=ts)
                counters["entries"] += 1
            elif prior_exposure > 0 and target == 0:
                ledger_equity = state_store.get_current_equity_usd()
                accounting = {"net_pnl_usd": equity - ledger_equity,
                              "gross_pnl_pct": None, "net_pnl_pct": None,
                              "fees_included": True, "cost_pct": cost_pct}
                state_store.record_trade_accounting(active_trade_id, accounting, ts=ts)
                state_store.log_event("EXIT", {"price": float(row.open), "equity_usd": equity,
                                      "reason": "TREND_OFF", "accounting": accounting},
                                      trade_id=active_trade_id, ts=ts)
                state_store.close_position(active_trade_id)
                active_trade_id = None
                counters["exits"] += 1
            elif target > 0 and not np.isclose(target, prior_exposure, atol=1e-12, rtol=0):
                meta = {"target_exposure": target, "strategy_package_id": strategy.PACKAGE_ID}
                state_store.resize_position(active_trade_id, target * equity, meta)
                state_store.log_event("REBALANCE", {"price": float(row.open),
                                      "from_exposure": prior_exposure, "to_exposure": target,
                                      "turnover": turnover, "equity_usd": equity},
                                      trade_id=active_trade_id, ts=ts)
                counters["rebalances"] += 1
            state_store.set_kv(STATE_KEY, json.dumps({"equity_usd": equity,
                               "target_exposure": target, "last_open": float(row.open),
                               "active_trade_id": active_trade_id, "ts": ts}, sort_keys=True))
            prior_exposure, prior_open = target, float(row.open)

        if prior_exposure > 0:
            close_cost = prior_exposure * cost_pct / 100
            daily_returns[-1] -= close_cost
            equity = initial_equity * float(np.prod(1 + np.asarray(daily_returns)))
            ts = _iso(frame.index[-1] + pd.Timedelta(seconds=1))
            ledger_equity = state_store.get_current_equity_usd()
            accounting = {"net_pnl_usd": equity - ledger_equity,
                          "gross_pnl_pct": None, "net_pnl_pct": None,
                          "fees_included": True, "cost_pct": cost_pct}
            state_store.record_trade_accounting(active_trade_id, accounting, ts=ts)
            state_store.log_event("EXIT", {"price": prior_open, "equity_usd": equity,
                                  "reason": "SEGMENT_END", "accounting": accounting},
                                  trade_id=active_trade_id, ts=ts)
            state_store.close_position(active_trade_id)
            counters["exits"] += 1
        state_store.log_event("REPLAY_COMPLETED", {"equity_usd": equity, "counters": counters},
                              ts=_iso(frame.index[-1] + pd.Timedelta(seconds=2)))
    returns = np.asarray(daily_returns)
    curve = initial_equity * np.cumprod(1 + returns)
    peaks = np.maximum.accumulate(np.r_[initial_equity, curve])
    drawdown = np.r_[initial_equity, curve] / peaks - 1
    return {"initial_equity_usd": initial_equity, "final_equity_usd": equity,
            "net_return_pct": (equity / initial_equity - 1) * 100,
            "max_drawdown_pct": abs(float(drawdown.min())) * 100,
            "daily_returns": returns, "counters": counters,
            "contract": strategy.FROZEN_CONTRACT.manifest()}
