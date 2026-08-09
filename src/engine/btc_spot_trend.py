"""Frozen BTC Spot long/cash volatility-scaled trend strategy."""
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Contract:
    slow_days: int = 50
    trend_buffer_pct: float = 1.0
    volatility_days: int = 30
    target_volatility_pct: float = 30.0
    maximum_exposure: float = 1.0
    market: str = "BTC/USDT:SPOT"

    def manifest(self):
        return asdict(self)


FROZEN_CONTRACT = Contract()
PACKAGE_ID = "btc_spot_vol_scaled_trend_v1"


def aggregate_closed_daily(source: pd.DataFrame) -> pd.DataFrame:
    frame = source.copy()
    if "ts" in frame.columns:
        frame["ts"] = pd.to_datetime(frame.ts)
        frame = frame.set_index("ts")
    frame.index = pd.to_datetime(frame.index)
    return frame.sort_index().resample("1D").agg({
        "open": "first", "high": "max", "low": "min", "close": "last",
        **({"volume": "sum"} if "volume" in frame.columns else {}),
    }).dropna(subset=["open", "close"])


def add_features(daily: pd.DataFrame, contract: Contract = FROZEN_CONTRACT) -> pd.DataFrame:
    result = daily.copy()
    result["trend_sma"] = result.close.rolling(
        contract.slow_days, min_periods=contract.slow_days,
    ).mean()
    result["realized_volatility"] = (
        result.close.pct_change(fill_method=None)
        .rolling(contract.volatility_days, min_periods=contract.volatility_days)
        .std() * np.sqrt(365)
    )
    result["trend_active"] = (
        result.close > result.trend_sma * (1 + contract.trend_buffer_pct / 100)
    )
    scaled = (contract.target_volatility_pct / 100 / result.realized_volatility).clip(
        lower=0, upper=contract.maximum_exposure,
    )
    result["target_exposure"] = result.trend_active.astype(float) * scaled
    result["target_exposure"] = result.target_exposure.fillna(0.0)
    return result


def decision_at(featured: pd.DataFrame, index: int = -1,
                contract: Contract = FROZEN_CONTRACT) -> dict:
    row = featured.iloc[index]
    return {
        "action": "LONG" if float(row.target_exposure) > 0 else "CASH",
        "target_exposure": float(row.target_exposure),
        "close": float(row.close),
        "trend_sma": None if pd.isna(row.trend_sma) else float(row.trend_sma),
        "realized_volatility": (
            None if pd.isna(row.realized_volatility) else float(row.realized_volatility)
        ),
        "signal_ts": str(featured.index[index]),
        "contract": contract.manifest(),
        "strategy_package_id": PACKAGE_ID,
    }
