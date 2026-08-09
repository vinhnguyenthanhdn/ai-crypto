"""Frozen production decision core for the funding-crowding trend sleeve."""
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .trend_sentiment import Contract as TrendContract
from .trend_sentiment import position_plan


@dataclass(frozen=True)
class Contract:
    top_n_liquid: int = 12
    max_concurrent: int = 2
    funding_crowding_z: float = 0.0
    risk_per_episode_pct: float = .25
    maximum_capital_fraction: float = .25
    breakout_lookback_bars: int = 4
    breakout_buffer_atr: float = .1
    base_stop_atr: float = 2.0
    maximum_stop_atr: float = 4.0
    risk_reward: float = 4.0
    maximum_hold_hours: int = 24
    cooldown_hours: int = 3
    minimum_trend_strength: float = 1.0

    def manifest(self) -> dict:
        return asdict(self)

    def trend_contract(self) -> TrendContract:
        return TrendContract(
            entry_mode="breakout_continuation", entry_timeframe="1h",
            breakout_lookback_bars=self.breakout_lookback_bars,
            breakout_buffer_atr=self.breakout_buffer_atr,
            base_stop_atr=self.base_stop_atr,
            maximum_stop_atr=self.maximum_stop_atr,
            risk_reward=self.risk_reward,
            maximum_hold_hours=self.maximum_hold_hours,
            cooldown_hours=self.cooldown_hours,
            risk_per_episode_pct=self.risk_per_episode_pct,
            maximum_capital_fraction=self.maximum_capital_fraction,
            minimum_trend_strength=self.minimum_trend_strength,
            sentiment_policy="none",
        )


FROZEN_CONTRACT = Contract()
PACKAGE_ID = "funding_crowding_trend_v1"
COMPOSITE_PACKAGE_ID = "composite_btc_trend_funding_crowding_v1"
COMPOSITE_BTC_WEIGHT = .5
COMPOSITE_FAST_WEIGHT = .5


def funding_allows(side: str, funding_z: float,
                   contract: Contract = FROZEN_CONTRACT) -> bool:
    if not np.isfinite(funding_z):
        return False
    if side == "LONG":
        return funding_z <= contract.funding_crowding_z
    if side == "SHORT":
        return funding_z >= -contract.funding_crowding_z
    raise ValueError("side must be LONG or SHORT")


def rank_entries(rows: dict[str, pd.Series], occupied: set[str], slots: int,
                 contract: Contract = FROZEN_CONTRACT) -> list[dict]:
    """Rank actionable signals inside the causal top-liquidity universe."""
    liquid = sorted(
        ((symbol, float(row.trailing_dollar_volume)) for symbol, row in rows.items()
         if np.isfinite(row.trailing_dollar_volume)),
        key=lambda item: item[1], reverse=True,
    )[:contract.top_n_liquid]
    candidates = []
    for symbol, dollar_volume in liquid:
        if symbol in occupied:
            continue
        row = rows[symbol]
        side = "LONG" if bool(row.long_signal) else "SHORT" if bool(row.short_signal) else None
        if side is None or not funding_allows(side, float(row.funding_z), contract):
            continue
        if not np.isfinite(row.entry_atr) or not np.isfinite(row.trend_strength):
            continue
        candidates.append({
            "symbol": symbol, "side": side, "atr": float(row.entry_atr),
            "trend_strength": float(row.trend_strength),
            "funding_z": float(row.funding_z), "dollar_volume": dollar_volume,
        })
    candidates.sort(key=lambda item: (item["trend_strength"], item["symbol"]), reverse=True)
    return candidates[:max(0, slots)]


def entry_plan(entry_price: float, decision: dict,
               contract: Contract = FROZEN_CONTRACT) -> dict:
    return position_plan(entry_price, decision["atr"], decision["side"],
                         decision["trend_strength"], contract.trend_contract())


def exit_decision(position: dict, row: pd.Series, timestamp: pd.Timestamp,
                  contract: Contract = FROZEN_CONTRACT) -> tuple[float, str] | None:
    side = position["side"]
    adverse = row.low <= position["stop_price"] if side == "LONG" else row.high >= position["stop_price"]
    favorable = row.high >= position["take_profit_price"] if side == "LONG" else row.low <= position["take_profit_price"]
    if adverse:
        return float(position["stop_price"]), "STOP_LOSS"
    if favorable:
        return float(position["take_profit_price"]), "TAKE_PROFIT"
    if timestamp - position["entry_time"] >= pd.Timedelta(hours=contract.maximum_hold_hours):
        return float(row.open), "TIMEOUT"
    if ((side == "LONG" and row.trend_direction < 0)
            or (side == "SHORT" and row.trend_direction > 0)):
        return float(row.open), "TREND_FLIP"
    return None
