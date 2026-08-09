"""Accelerated Paper lifecycle cho frozen staggered-pullback strategy.

Engine dùng production signal/risk primitives và toàn bộ SQLite state/event
lifecycle. Khác runtime live duy nhất là clock do caller điều khiển và market
bars đến từ dataset lịch sử thay vì exchange.
"""
from dataclasses import dataclass

import pandas as pd

from .. import config, state_store
from ..engine import risk
from ..engine import staggered_pullback as strategy


STATE_KEY = "staggered_pullback_machine_state"


@dataclass
class SimulatedClock:
    current: pd.Timestamp | None = None

    def set(self, value) -> pd.Timestamp:
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        else:
            timestamp = timestamp.tz_convert("UTC")
        self.current = timestamp
        return timestamp

    def iso(self) -> str:
        if self.current is None:
            raise RuntimeError("simulated clock chưa được khởi tạo")
        return self.current.isoformat()


def _default_machine_state() -> dict:
    return {
        "active_side": None,
        "fills_in_excursion": 0,
        "excursion_id": 0,
        "pending_entry": None,
        "pending_exit": False,
        "completed": False,
    }


def _load_machine_state() -> dict:
    import json

    raw = state_store.get_kv(STATE_KEY)
    return {**_default_machine_state(), **(json.loads(raw) if raw else {})}


def _save_machine_state(machine: dict):
    import json

    state_store.set_kv(STATE_KEY, json.dumps(machine, sort_keys=True))


def _normalise_timestamp(value) -> str:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("UTC").tz_localize(None)
    return str(timestamp)


def _position_side(position: dict) -> str:
    side = (position.get("position_meta") or {}).get("side")
    if side not in ("LONG", "SHORT"):
        raise AssertionError(f"position thiếu side hợp lệ: {position['trade_id']}")
    return side


def run_accelerated_replay(
    featured_bars: pd.DataFrame,
    start,
    end,
    *,
    symbol: str = "BTC/USDT",
    contract: strategy.Contract = strategy.FROZEN_CONTRACT,
    risk_per_excursion_pct: float = 1.0,
    clock: SimulatedClock | None = None,
    log_every_bar: bool = True,
) -> dict:
    """Replay một timeline liên tục qua SQLite lifecycle production.

    DB phải rỗng để một run không trộn state với runtime/experiment khác.
    Caller chọn ``config.DB_PATH`` trước khi gọi hàm.
    """
    clock = clock or SimulatedClock()
    frame = featured_bars[
        (featured_bars.index >= pd.Timestamp(start))
        & (featured_bars.index < pd.Timestamp(end))
    ]
    rows = list(frame.itertuples())
    if not rows:
        raise ValueError("không có bar trong khoảng replay")

    closed_trades = []
    counters = {
        "bars": 0, "entries": 0, "exits": 0, "stops": 0,
        "mean_exits": 0, "segment_end_exits": 0, "risk_rejections": 0,
    }

    def close_position(position: dict, timestamp, price: float, reason: str):
        clock.set(timestamp)
        side = _position_side(position)
        equity_before = state_store.get_current_equity_usd()
        accounting = risk.compute_trade_accounting(
            float(position["entry_price"]), float(price), float(position["size_usd"]),
            side=side.lower(), fee_pct=config.FEE_PCT,
            slippage_pct=config.SLIPPAGE_PCT, equity_before_usd=equity_before,
        )
        ledger = state_store.record_trade_accounting(
            position["trade_id"], accounting, ts=clock.iso(),
        )
        accounting = ledger["accounting"]
        state_store.close_position(position["trade_id"])
        state_store.record_exit_now(ts=clock.iso())
        state_store.log_signal(
            symbol, price, "EXIT", 0.0, {"staggered_pullback": 100.0},
            notes=reason, ts=clock.iso(),
        )
        meta = position.get("position_meta") or {}
        payload = {
            "market": {"price": float(price)},
            "reason": reason,
            "side": side,
            "excursion_id": meta["excursion_id"],
            "pnl_pct": accounting["return_on_equity_pct"],
            "net_pnl_pct": accounting["net_pnl_pct"],
            "gross_pnl_pct": accounting["gross_pnl_pct"],
            "pnl_usd": accounting["net_pnl_usd"],
            "accounting": accounting,
        }
        state_store.log_event("EXIT", payload, trade_id=position["trade_id"], ts=clock.iso())
        closed_trades.append({
            "trade_id": position["trade_id"],
            "side": side,
            "excursion_id": meta["excursion_id"],
            "entry_ts": _normalise_timestamp(position["entry_time"]),
            "entry_price": float(position["entry_price"]),
            "stop": float(position["stop_price"]),
            "exit_ts": _normalise_timestamp(timestamp),
            "exit_price": float(price),
            "exit_reason": reason,
            "size_usd": float(position["size_usd"]),
            "risk_usd": float(meta["risk_usd"]),
            "accounting": accounting,
        })
        counters["exits"] += 1
        if reason == "INITIAL_STOP":
            counters["stops"] += 1
        elif reason == "MEAN_EXIT":
            counters["mean_exits"] += 1
        elif reason == "SEGMENT_END":
            counters["segment_end_exits"] += 1

    with state_store.session() as conn:
        existing = conn.execute(
            "SELECT (SELECT COUNT(*) FROM event_log), "
            "(SELECT COUNT(*) FROM position_state), "
            "(SELECT COUNT(*) FROM equity_ledger)"
        ).fetchone()
        if any(existing):
            raise RuntimeError("accelerated replay yêu cầu DB rỗng")
        machine = _default_machine_state()
        _save_machine_state(machine)
        clock.set(rows[0].Index)
        state_store.log_event(
            "REPLAY_STARTED",
            {
                "start": str(pd.Timestamp(start)), "end": str(pd.Timestamp(end)),
                "contract": contract.manifest(), "clock": "simulated",
                "strategy_package_id": config.STAGGERED_PULLBACK_PACKAGE_ID,
            },
            ts=clock.iso(),
        )

        for index, row in enumerate(rows):
            counters["bars"] += 1
            machine = _load_machine_state()
            bar_time = pd.Timestamp(row.Index)
            clock.set(bar_time)

            if machine["pending_exit"]:
                for position in state_store.get_open_positions():
                    close_position(position, bar_time, float(row.open), "MEAN_EXIT")
                machine.update({
                    "pending_exit": False, "active_side": None,
                    "fills_in_excursion": 0,
                })

            pending = machine.get("pending_entry")
            if pending is not None:
                side = pending["side"]
                open_positions = state_store.get_open_positions()
                committed_risk = sum(
                    float((position.get("position_meta") or {}).get("risk_usd", 0.0))
                    for position in open_positions
                    if (position.get("position_meta") or {}).get("excursion_id")
                    == machine["excursion_id"]
                )
                plan = strategy.compute_tranche_plan(
                    float(row.open), float(pending["signal_atr"]),
                    state_store.get_current_equity_usd(), side=side,
                    committed_excursion_risk_usd=committed_risk,
                    risk_per_excursion_pct=risk_per_excursion_pct,
                    contract=contract,
                )
                daily_halted = state_store.is_trading_halted_at(clock.iso())
                drawdown_halted = state_store.get_max_drawdown_pct() >= config.MAX_DRAWDOWN_PCT
                if plan["size_usd"] <= 0 or daily_halted or drawdown_halted:
                    reason = (
                        "daily_loss_halt" if daily_halted
                        else ("max_drawdown_halt" if drawdown_halted else "risk_budget_exhausted")
                    )
                    state_store.log_event(
                        "RISK_REJECTED",
                        {"reason": reason, "position_plan": plan, "side": side},
                        ts=clock.iso(),
                    )
                    counters["risk_rejections"] += 1
                else:
                    state_store.log_event(
                        "RISK_CHECKED",
                        {"decision": "PASS", "position_plan": plan, "side": side},
                        ts=clock.iso(),
                    )
                    position_meta = {
                        "side": side,
                        "excursion_id": machine["excursion_id"],
                        "risk_usd": plan["risk_usd"],
                        "risk_plan": plan,
                        "signal_ts": pending["signal_ts"],
                    }
                    trade_id = state_store.open_position(
                        symbol, float(row.open), clock.iso(), abs(float(pending["signal_z"])),
                        plan["stop_price"], None, plan["size_usd"],
                        tp_reason="ZSCORE_MEAN_EXIT",
                        scoring_profile="staggered_pullback",
                        position_meta=position_meta,
                    )
                    state_store.log_signal(
                        symbol, float(row.open), side, abs(float(pending["signal_z"])),
                        {"staggered_pullback": 100.0},
                        notes="NEXT_4H_OPEN", ts=clock.iso(),
                    )
                    state_store.log_event(
                        "ENTRY",
                        {
                            "market": {"price": float(row.open)}, "side": side,
                            "excursion_id": machine["excursion_id"],
                            "signal": {
                                "ts": pending["signal_ts"], "z": pending["signal_z"],
                                "atr": pending["signal_atr"],
                            },
                            "risk": {
                                "position_usd": plan["size_usd"],
                                "risk_usd": plan["risk_usd"], "sl": plan["stop_price"],
                            },
                            "model": config.STAGGERED_PULLBACK_PACKAGE_ID,
                            "scoring_profile": "staggered_pullback",
                        },
                        trade_id=trade_id, ts=clock.iso(),
                    )
                    machine["fills_in_excursion"] += 1
                    counters["entries"] += 1
                machine["pending_entry"] = None

            for position in state_store.get_open_positions():
                side = _position_side(position)
                stopped = (
                    float(row.low) <= float(position["stop_price"])
                    if side == "LONG"
                    else float(row.high) >= float(position["stop_price"])
                )
                if stopped:
                    close_position(
                        position, bar_time, float(position["stop_price"]), "INITIAL_STOP",
                    )

            close_time = bar_time + pd.Timedelta(hours=4)
            clock.set(close_time)
            features = {
                "open": float(row.open), "high": float(row.high),
                "low": float(row.low), "close": float(row.close),
                "z": None if pd.isna(row.z) else float(row.z),
                "atr": None if pd.isna(row.atr) else float(row.atr),
                "trend_ema": None if pd.isna(row.trend_ema) else float(row.trend_ema),
            }
            if log_every_bar:
                state_store.log_feature_snapshot(
                    symbol, float(row.close), features,
                    lineage={
                        "source_timeframe": contract.source_timeframe,
                        "signal_timeframe": contract.timeframe,
                        "transformation_version": "staggered_pullback_features_v1",
                        "strategy_package_id": config.STAGGERED_PULLBACK_PACKAGE_ID,
                        "engine_version": config.PAPER_ENGINE_VERSION,
                        "clock": "simulated",
                        "candle_policy": "closed_4h_only",
                    },
                    ts=clock.iso(),
                )
                state_store.log_event(
                    "FEATURE_UPDATED", {"feature": features}, ts=clock.iso(),
                )

            if index + 1 < len(rows) and not pd.isna(row.z) and not pd.isna(row.atr):
                if machine["active_side"] is not None and strategy.exit_signal(
                    machine["active_side"], float(row.z), contract,
                ):
                    if state_store.get_open_positions():
                        machine["pending_exit"] = True
                        state_store.log_event(
                            "EXIT_SCHEDULED",
                            {"side": machine["active_side"], "z": float(row.z),
                             "execution": "NEXT_4H_OPEN"},
                            ts=clock.iso(),
                        )
                    else:
                        machine.update({"active_side": None, "fills_in_excursion": 0})
                else:
                    signal = strategy.entry_signal(row, contract)
                    if machine["active_side"] is None and signal is not None:
                        machine["excursion_id"] += 1
                        machine["active_side"] = signal
                    if (
                        signal is not None
                        and signal == machine["active_side"]
                        and machine["fills_in_excursion"] < contract.max_tranches
                    ):
                        machine["pending_entry"] = {
                            "side": signal, "signal_atr": float(row.atr),
                            "signal_z": float(row.z), "signal_ts": clock.iso(),
                        }
                        state_store.log_event(
                            "ENTRY_SCHEDULED",
                            {"side": signal, "z": float(row.z),
                             "atr": float(row.atr), "execution": "NEXT_4H_OPEN",
                             "excursion_id": machine["excursion_id"]},
                            ts=clock.iso(),
                        )
            _save_machine_state(machine)
            if counters["bars"] % 1000 == 0:
                conn.commit()

        last_time = pd.Timestamp(rows[-1].Index)
        for position in state_store.get_open_positions():
            close_position(position, last_time, float(rows[-1].close), "SEGMENT_END")
        machine = _load_machine_state()
        machine.update({
            "pending_entry": None, "pending_exit": False,
            "active_side": None, "fills_in_excursion": 0, "completed": True,
        })
        _save_machine_state(machine)
        clock.set(last_time + pd.Timedelta(hours=4))
        state_store.log_event(
            "REPLAY_COMPLETED",
            {"counters": counters, "final_equity_usd": state_store.get_current_equity_usd()},
            ts=clock.iso(),
        )

        event_counts = dict(conn.execute(
            "SELECT type, COUNT(*) FROM event_log GROUP BY type ORDER BY type"
        ).fetchall())
        feature_count = conn.execute("SELECT COUNT(*) FROM feature_snapshot").fetchone()[0]
        ledger_count = conn.execute("SELECT COUNT(*) FROM equity_ledger").fetchone()[0]
        open_count = conn.execute("SELECT COUNT(*) FROM position_state").fetchone()[0]

    initial_equity = float(config.ACCOUNT_EQUITY_USD)
    final_equity = state_store.get_current_equity_usd()
    return {
        "contract": contract.manifest(),
        "clock": "simulated",
        "start": str(pd.Timestamp(start)), "end": str(pd.Timestamp(end)),
        "counters": counters,
        "event_counts": event_counts,
        "feature_snapshot_count": feature_count,
        "equity_ledger_count": ledger_count,
        "open_position_count": open_count,
        "initial_equity_usd": initial_equity,
        "final_equity_usd": round(final_equity, 8),
        "net_return_pct": round((final_equity / initial_equity - 1) * 100, 6),
        "max_drawdown_pct": state_store.get_max_drawdown_pct(),
        "trades": closed_trades,
        "machine_state": machine,
    }
