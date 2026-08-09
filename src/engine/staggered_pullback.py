"""Production core cho staggered slow trend-pullback 4h.

Module thuần, không đọc config/state DB và không gửi order. Runtime hoặc replay
phải truyền contract/state tường minh để cùng một lifecycle dùng được ở cả hai.
"""
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Contract:
    timeframe: str = "4h"
    source_timeframe: str = "5m"
    z_lookback_bars: int = 60
    trend_ema_bars: int = 180
    entry_z: float = 2.0
    exit_z: float = 0.5
    stop_atr: float = 5.0
    atr_bars: int = 14
    max_tranches: int = 5
    round_trip_cost_pct: float = 0.30

    def manifest(self) -> dict:
        return asdict(self)


FROZEN_CONTRACT = Contract()


def aggregate_closed_4h(source: pd.DataFrame) -> pd.DataFrame:
    """Aggregate nến 5m thành nến 4h hoàn chỉnh, bỏ mọi bucket thiếu bar."""
    frame = source.copy()
    if "ts" in frame.columns:
        frame["ts"] = pd.to_datetime(frame["ts"])
        frame = frame.set_index("ts")
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("source cần DatetimeIndex hoặc cột ts")
    bars = frame.resample("4h", origin="epoch", label="left", closed="left").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), count=("close", "count"),
    )
    return bars[bars["count"] == 48].drop(columns="count").dropna().copy()


def add_features(bars: pd.DataFrame, contract: Contract = FROZEN_CONTRACT) -> pd.DataFrame:
    """Feature causal trên bar đã đóng; không center/backfill từ tương lai."""
    result = bars.copy()
    mean = result.close.rolling(contract.z_lookback_bars).mean()
    std = result.close.rolling(contract.z_lookback_bars).std().replace(0, np.nan)
    result["z"] = (result.close - mean) / std
    result["trend_ema"] = result.close.ewm(span=contract.trend_ema_bars, adjust=False).mean()
    prior_close = result.close.shift(1)
    true_range = pd.concat(
        [result.high - result.low, (result.high - prior_close).abs(), (result.low - prior_close).abs()],
        axis=1,
    ).max(axis=1)
    result["atr"] = true_range.ewm(
        alpha=1 / contract.atr_bars, adjust=False, min_periods=contract.atr_bars,
    ).mean()
    return result


def entry_signal(row, contract: Contract = FROZEN_CONTRACT) -> str | None:
    """Side tại close bar hiện tại; caller chỉ được fill từ bar kế tiếp."""
    if pd.isna(row.z) or pd.isna(row.atr):
        return None
    if row.close >= row.trend_ema and row.z <= -contract.entry_z:
        return "LONG"
    if row.close < row.trend_ema and row.z >= contract.entry_z:
        return "SHORT"
    return None


def exit_signal(side: str, zscore: float, contract: Contract = FROZEN_CONTRACT) -> bool:
    if pd.isna(zscore):
        return False
    return zscore >= contract.exit_z if side == "LONG" else zscore <= -contract.exit_z


def compute_tranche_plan(
    entry_price: float,
    atr: float,
    account_equity_usd: float,
    *,
    side: str,
    committed_excursion_risk_usd: float = 0.0,
    risk_per_excursion_pct: float = 1.0,
    contract: Contract = FROZEN_CONTRACT,
) -> dict:
    """Chia risk của một excursion, không cấp nguyên risk budget cho từng ticket."""
    if entry_price <= 0 or atr <= 0 or account_equity_usd <= 0:
        raise ValueError("entry_price, atr và account_equity_usd phải dương")
    if side not in ("LONG", "SHORT"):
        raise ValueError("side phải là LONG hoặc SHORT")
    stop_distance = contract.stop_atr * atr
    stop_price = entry_price - stop_distance if side == "LONG" else entry_price + stop_distance
    excursion_budget = account_equity_usd * risk_per_excursion_pct / 100
    tranche_budget = excursion_budget / contract.max_tranches
    remaining_budget = max(0.0, excursion_budget - committed_excursion_risk_usd)
    risk_usd = min(tranche_budget, remaining_budget)
    size_usd = risk_usd / (stop_distance / entry_price) if risk_usd else 0.0
    capital_cap = account_equity_usd / contract.max_tranches
    size_usd = min(size_usd, capital_cap)
    actual_risk_usd = size_usd * stop_distance / entry_price
    return {
        "side": side,
        "stop_price": round(stop_price, 8),
        "size_usd": round(size_usd, 8),
        "risk_usd": round(actual_risk_usd, 8),
        "excursion_risk_budget_usd": round(excursion_budget, 8),
        "committed_excursion_risk_usd": round(committed_excursion_risk_usd, 8),
        "remaining_excursion_risk_usd": round(max(0.0, remaining_budget - actual_risk_usd), 8),
        "capital_cap_usd": round(capital_cap, 8),
    }


def replay(
    featured_bars: pd.DataFrame,
    start,
    end,
    contract: Contract = FROZEN_CONTRACT,
) -> list[dict]:
    """Replay frozen lifecycle; một fill tối đa mỗi bar, adverse stop intrabar."""
    frame = featured_bars[(featured_bars.index >= start) & (featured_bars.index < end)]
    rows = list(frame.itertuples())
    trades, positions = [], []
    pending_entry, pending_exit = None, False
    active_side, fills_in_excursion, excursion_id = None, 0, 0

    def close(position, timestamp, price, reason):
        if position["side"] == "LONG":
            gross = (price / position["entry_price"] - 1) * 100
        else:
            gross = (position["entry_price"] - price) / position["entry_price"] * 100
        trades.append({
            **position, "exit_ts": timestamp, "exit_price": float(price),
            "exit_reason": reason, "net_return_pct": gross - contract.round_trip_cost_pct,
        })

    for index, row in enumerate(rows):
        if pending_exit:
            for position in positions:
                close(position, row.Index, row.open, "MEAN_EXIT")
            positions = []
            pending_exit = False
            active_side, fills_in_excursion = None, 0

        if pending_entry is not None:
            side = pending_entry
            entry_price = float(row.open)
            signal_atr = float(rows[index - 1].atr)
            stop = (
                entry_price - contract.stop_atr * signal_atr
                if side == "LONG" else entry_price + contract.stop_atr * signal_atr
            )
            positions.append({
                "side": side, "entry_ts": row.Index, "entry_price": entry_price,
                "stop": stop, "tranche_capital_fraction": 1 / contract.max_tranches,
                "excursion_id": excursion_id,
            })
            fills_in_excursion += 1
            pending_entry = None

        survivors = []
        for position in positions:
            stopped = (
                row.low <= position["stop"] if position["side"] == "LONG"
                else row.high >= position["stop"]
            )
            if stopped:
                close(position, row.Index, position["stop"], "INITIAL_STOP")
            else:
                survivors.append(position)
        positions = survivors

        if index + 1 >= len(rows) or pd.isna(row.z) or pd.isna(row.atr):
            continue
        if active_side is not None and exit_signal(active_side, row.z, contract):
            if positions:
                pending_exit = True
            else:
                active_side, fills_in_excursion = None, 0
            continue

        signal = entry_signal(row, contract)
        if active_side is None and signal is not None:
            excursion_id += 1
            active_side = signal
        if signal == active_side and fills_in_excursion < contract.max_tranches:
            pending_entry = signal

    if rows:
        for position in positions:
            close(position, rows[-1].Index, rows[-1].close, "SEGMENT_END")
    return trades
