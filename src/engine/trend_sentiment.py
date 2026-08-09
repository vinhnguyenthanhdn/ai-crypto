"""Multi-timeframe directional strategy with causal sentiment and adaptive risk.

The package is research-first.  It does not place orders and is intentionally
separate from the frozen champion until temporal, cost and Paper gates pass.
"""
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Contract:
    entry_mode: str = "pullback_reclaim"
    entry_timeframe: str = "1h"
    entry_ema_bars: int = 20
    context_fast_bars: int = 20
    context_slow_bars: int = 100
    daily_sma_days: int = 50
    pullback_buffer_atr: float = 0.25
    base_stop_atr: float = 1.5
    maximum_stop_atr: float = 3.0
    trend_strength_for_max_stop: float = 2.0
    risk_reward: float = 1.5
    maximum_hold_hours: int = 48
    cooldown_hours: int = 6
    risk_per_episode_pct: float = 0.5
    maximum_capital_fraction: float = 0.5
    sentiment_policy: str = "contrarian_veto"
    sentiment_extreme_low: float = 20.0
    sentiment_extreme_high: float = 80.0
    news_policy: str = "neutral_without_causal_archive"
    session_hours: int = 4
    volume_lookback_bars: int = 1440
    volume_quantile: float = 0.80
    momentum_atr_min: float = 0.10
    breakout_lookback_bars: int = 8
    breakout_buffer_atr: float = 0.0
    minimum_trend_strength: float = 0.0

    def manifest(self) -> dict:
        return asdict(self)


PACKAGE_ID = "trend_sentiment_adaptive_risk_v1"


def _bars(source: pd.DataFrame, frequency: str) -> pd.DataFrame:
    frame = source.copy()
    if "ts" in frame.columns:
        frame["ts"] = pd.to_datetime(frame.ts, utc=True)
        frame = frame.set_index("ts")
    frame.index = pd.to_datetime(frame.index, utc=True)
    aggregation = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in frame.columns:
        aggregation["volume"] = "sum"
    return frame.sort_index().resample(frequency, origin="epoch", label="left", closed="left").agg(
        aggregation
    ).dropna(subset=["open", "high", "low", "close"])


def _atr(frame: pd.DataFrame, bars: int = 14) -> pd.Series:
    previous = frame.close.shift(1)
    true_range = pd.concat([
        frame.high - frame.low,
        (frame.high - previous).abs(),
        (frame.low - previous).abs(),
    ], axis=1).max(axis=1)
    return true_range.ewm(alpha=1 / bars, adjust=False, min_periods=bars).mean()


def _available_context(frame: pd.DataFrame, delay: pd.Timedelta, prefix: str) -> pd.DataFrame:
    result = frame.copy()
    result.index = result.index + delay
    return result.add_prefix(prefix)


def _asof(left: pd.DataFrame, right: pd.DataFrame) -> pd.DataFrame:
    left_named = left.reset_index().rename(columns={left.index.name or "index": "ts"})
    right_named = right.reset_index().rename(columns={right.index.name or "index": "ts"})
    return pd.merge_asof(
        left_named.sort_values("ts"), right_named.sort_values("ts"), on="ts", direction="backward"
    ).set_index("ts")


def add_features(source_5m: pd.DataFrame, sentiment: pd.DataFrame | None,
                 contract: Contract) -> pd.DataFrame:
    """Build causal execution/context features.

    A 4h bar becomes visible at its next 4h boundary and a daily bar at the next
    UTC day boundary. Sentiment/news can only be joined at or after their source
    timestamp.
    """
    execution = _bars(source_5m, contract.entry_timeframe)
    execution["entry_ema"] = execution.close.ewm(
        span=contract.entry_ema_bars, adjust=False, min_periods=contract.entry_ema_bars,
    ).mean()
    execution["entry_atr"] = _atr(execution)

    context = _bars(source_5m, "4h")
    context["fast_ema"] = context.close.ewm(
        span=contract.context_fast_bars, adjust=False,
        min_periods=contract.context_fast_bars,
    ).mean()
    context["slow_ema"] = context.close.ewm(
        span=contract.context_slow_bars, adjust=False,
        min_periods=contract.context_slow_bars,
    ).mean()
    context["atr"] = _atr(context)
    context["direction"] = np.sign(context.fast_ema - context.slow_ema)
    context["strength"] = (context.fast_ema - context.slow_ema).abs() / context.atr.replace(0, np.nan)

    daily = _bars(source_5m, "1D")
    daily["sma"] = daily.close.rolling(
        contract.daily_sma_days, min_periods=contract.daily_sma_days,
    ).mean()
    daily["atr"] = _atr(daily)
    daily["direction"] = np.sign(daily.close - daily.sma)
    daily["strength"] = (daily.close - daily.sma).abs() / daily.atr.replace(0, np.nan)

    result = _asof(execution, _available_context(context, pd.Timedelta(hours=4), "ctx_"))
    result = _asof(result, _available_context(daily, pd.Timedelta(days=1), "day_"))

    if sentiment is not None and not sentiment.empty:
        sent = sentiment.copy()
        if "ts" in sent.columns:
            sent["ts"] = pd.to_datetime(sent.ts, utc=True)
            sent = sent.set_index("ts")
        sent.index = pd.to_datetime(sent.index, utc=True)
        result = _asof(result, sent[["sentiment_value"]].sort_index())
    else:
        result["sentiment_value"] = np.nan

    result["trend_direction"] = np.where(
        (result.ctx_direction == result.day_direction) & result.ctx_direction.notna(),
        result.ctx_direction, 0,
    )
    result["trend_strength"] = result[["ctx_strength", "day_strength"]].mean(axis=1)
    normalized = (result.trend_strength / contract.trend_strength_for_max_stop).clip(0, 1)
    result["stop_atr_multiple"] = (
        contract.base_stop_atr
        + normalized * (contract.maximum_stop_atr - contract.base_stop_atr)
    )

    previous_close = result.close.shift(1)
    previous_ema = result.entry_ema.shift(1)
    long_reclaim = (
        (result.low <= result.entry_ema + contract.pullback_buffer_atr * result.entry_atr)
        & (result.close > result.entry_ema)
        & (result.close > result.open)
        & (previous_close <= previous_ema)
    )
    short_reclaim = (
        (result.high >= result.entry_ema - contract.pullback_buffer_atr * result.entry_atr)
        & (result.close < result.entry_ema)
        & (result.close < result.open)
        & (previous_close >= previous_ema)
    )
    long_allowed = result.sentiment_value.isna() | (
        result.sentiment_value <= contract.sentiment_extreme_high
    )
    short_allowed = result.sentiment_value.isna() | (
        result.sentiment_value >= contract.sentiment_extreme_low
    )
    if contract.sentiment_policy == "none":
        long_allowed = short_allowed = pd.Series(True, index=result.index)
    if contract.entry_mode == "pullback_reclaim":
        long_trigger, short_trigger = long_reclaim, short_reclaim
    elif contract.entry_mode == "breakout_continuation":
        result["prior_breakout_high"] = result.high.shift(1).rolling(
            contract.breakout_lookback_bars,
            min_periods=contract.breakout_lookback_bars,
        ).max()
        result["prior_breakout_low"] = result.low.shift(1).rolling(
            contract.breakout_lookback_bars,
            min_periods=contract.breakout_lookback_bars,
        ).min()
        buffer = contract.breakout_buffer_atr * result.entry_atr
        long_trigger = result.close > result.prior_breakout_high + buffer
        short_trigger = result.close < result.prior_breakout_low - buffer
    elif contract.entry_mode == "session_momentum":
        session_open = (
            (result.index.hour % contract.session_hours == 0) & (result.index.minute == 0)
        )
        volume_threshold = result.volume.shift(1).rolling(
            contract.volume_lookback_bars, min_periods=contract.volume_lookback_bars,
        ).quantile(contract.volume_quantile)
        momentum_atr = (result.close - result.open) / result.entry_atr.replace(0, np.nan)
        high_volume = result.volume >= volume_threshold
        long_trigger = session_open & high_volume & (momentum_atr >= contract.momentum_atr_min)
        short_trigger = session_open & high_volume & (momentum_atr <= -contract.momentum_atr_min)
    else:
        raise ValueError(f"unsupported entry_mode: {contract.entry_mode}")
    strong_enough = result.trend_strength >= contract.minimum_trend_strength
    result["long_signal"] = (
        (result.trend_direction > 0) & strong_enough & long_trigger & long_allowed
    )
    result["short_signal"] = (
        (result.trend_direction < 0) & strong_enough & short_trigger & short_allowed
    )
    return result


def position_plan(entry_price: float, atr: float, side: str, trend_strength: float,
                  contract: Contract) -> dict:
    normalized = min(1.0, max(0.0, trend_strength / contract.trend_strength_for_max_stop))
    stop_multiple = contract.base_stop_atr + normalized * (
        contract.maximum_stop_atr - contract.base_stop_atr
    )
    stop_distance = atr * stop_multiple
    reward_distance = stop_distance * contract.risk_reward
    if side == "LONG":
        stop, take_profit = entry_price - stop_distance, entry_price + reward_distance
    elif side == "SHORT":
        stop, take_profit = entry_price + stop_distance, entry_price - reward_distance
    else:
        raise ValueError("side must be LONG or SHORT")
    stop_fraction = stop_distance / entry_price
    capital_fraction = min(
        contract.maximum_capital_fraction,
        (contract.risk_per_episode_pct / 100) / stop_fraction if stop_fraction > 0 else 0,
    )
    return {
        "stop_price": float(stop), "take_profit_price": float(take_profit),
        "stop_atr_multiple": float(stop_multiple), "capital_fraction": float(capital_fraction),
        "risk_reward": contract.risk_reward,
    }
