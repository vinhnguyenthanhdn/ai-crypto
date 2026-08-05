"""Indicator + Score Engine cho lớp Technical (xem plan-02.md, phần Score Engine).

Điểm tối đa 100, mỗi tín hiệu bullish cộng điểm tương ứng bảng trong plan-02.md.
Đa khung thời gian (1m/5m/15m) dùng làm bộ lọc xác nhận: nếu các khung không
đồng thuận hướng trend, phần điểm Trend/MACD bị giảm theo tỷ lệ đồng thuận.
"""
import numpy as np
import pandas as pd
import ta

SCORE_WEIGHTS = {
    "ema_trend": 15,
    "macd_cross": 15,
    "rsi_oversold": 10,
    "adx_strong": 10,
    "volume_spike": 10,
    "supertrend": 20,
    "vwap": 10,
    "pattern": 10,
}
assert sum(SCORE_WEIGHTS.values()) == 100


def to_dataframe(ohlcv):
    df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    return df


def _supertrend(df, period=10, multiplier=3.0):
    atr = ta.volatility.average_true_range(df["high"], df["low"], df["close"], window=period)
    hl2 = (df["high"] + df["low"]) / 2
    upper = hl2 + multiplier * atr
    lower = hl2 - multiplier * atr

    direction = pd.Series(index=df.index, dtype="int64")
    direction.iloc[0] = 1
    final_upper = upper.copy()
    final_lower = lower.copy()

    for i in range(1, len(df)):
        if df["close"].iloc[i - 1] > final_upper.iloc[i - 1]:
            direction.iloc[i] = 1
        elif df["close"].iloc[i - 1] < final_lower.iloc[i - 1]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i - 1]
            if direction.iloc[i] == 1 and lower.iloc[i] < final_lower.iloc[i - 1]:
                final_lower.iloc[i] = final_lower.iloc[i - 1]
            if direction.iloc[i] == -1 and upper.iloc[i] > final_upper.iloc[i - 1]:
                final_upper.iloc[i] = final_upper.iloc[i - 1]
    return direction


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema20"] = ta.trend.ema_indicator(df["close"], window=20)
    df["ema50"] = ta.trend.ema_indicator(df["close"], window=50)
    df["ema200"] = ta.trend.ema_indicator(df["close"], window=200)
    df["rsi"] = ta.momentum.rsi(df["close"], window=14)
    macd = ta.trend.MACD(df["close"])
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["adx"] = ta.trend.adx(df["high"], df["low"], df["close"], window=14)
    df["atr"] = ta.volatility.average_true_range(df["high"], df["low"], df["close"], window=14)
    df["vwap"] = ta.volume.volume_weighted_average_price(
        df["high"], df["low"], df["close"], df["volume"], window=14
    )
    df["vol_sma20"] = df["volume"].rolling(20).mean()
    df["supertrend_dir"] = _supertrend(df)
    return df


def _trend_direction(df: pd.DataFrame, idx: int = -1) -> int:
    last = df.iloc[idx]
    if pd.isna(last["ema20"]) or pd.isna(last["ema50"]):
        return 0
    return 1 if last["ema20"] > last["ema50"] else -1


def _detect_bullish_pattern(df: pd.DataFrame, idx: int = -1) -> bool:
    """Bullish engulfing đơn giản trên 2 nến gần nhất."""
    if len(df) < 2 or idx == 0:
        return False
    prev, last = df.iloc[idx - 1], df.iloc[idx]
    prev_bearish = prev["close"] < prev["open"]
    last_bullish = last["close"] > last["open"]
    engulfs = last["close"] >= prev["open"] and last["open"] <= prev["close"]
    return bool(prev_bearish and last_bullish and engulfs)


def score_from_indicators(primary_enriched: pd.DataFrame, idx: int = -1, agreement_ratio: float = 1.0) -> dict:
    """Tính breakdown/raw từ dataframe ĐÃ `add_indicators()` — không tính lại
    indicator, chỉ đọc theo `idx` (mặc định -1 = bar cuối). Tách riêng khỏi
    `compute_technical_score` để Backtest Engine (mục 11) có thể gọi lại nhiều
    lần trên cùng 1 dataframe đã enrich sẵn (O(1)/bar) thay vì recompute indicator
    trên slice tăng dần mỗi bar (O(n) x O(n) = quá chậm cho lịch sử dài).
    """
    last = primary_enriched.iloc[idx]
    breakdown = {}

    bullish_stack = last["ema20"] > last["ema50"] > last["ema200"] if not pd.isna(last["ema200"]) else False
    breakdown["ema_trend"] = SCORE_WEIGHTS["ema_trend"] * agreement_ratio if bullish_stack else 0

    macd_golden_cross = False
    if len(primary_enriched) >= 2 and idx != 0:
        prev = primary_enriched.iloc[idx - 1]
        if not pd.isna(prev["macd"]) and not pd.isna(last["macd"]):
            macd_golden_cross = prev["macd"] <= prev["macd_signal"] and last["macd"] > last["macd_signal"]
    breakdown["macd_cross"] = SCORE_WEIGHTS["macd_cross"] * agreement_ratio if macd_golden_cross else 0

    rsi_recovering = False
    if len(primary_enriched) >= 2 and idx != 0:
        prev_rsi, last_rsi = primary_enriched.iloc[idx - 1]["rsi"], last["rsi"]
        rsi_recovering = not pd.isna(prev_rsi) and not pd.isna(last_rsi) and prev_rsi < 30 <= last_rsi
    breakdown["rsi_oversold"] = SCORE_WEIGHTS["rsi_oversold"] if rsi_recovering else 0

    breakdown["adx_strong"] = SCORE_WEIGHTS["adx_strong"] if (not pd.isna(last["adx"]) and last["adx"] > 25) else 0

    vol_spike = not pd.isna(last["vol_sma20"]) and last["vol_sma20"] > 0 and last["volume"] > 1.5 * last["vol_sma20"]
    breakdown["volume_spike"] = SCORE_WEIGHTS["volume_spike"] if vol_spike else 0

    breakdown["supertrend"] = SCORE_WEIGHTS["supertrend"] if last["supertrend_dir"] == 1 else 0

    breakdown["vwap"] = SCORE_WEIGHTS["vwap"] if (not pd.isna(last["vwap"]) and last["close"] > last["vwap"]) else 0

    breakdown["pattern"] = SCORE_WEIGHTS["pattern"] if _detect_bullish_pattern(primary_enriched, idx) else 0

    total = round(sum(breakdown.values()), 2)

    # Feature Store (xem plan-02.md, mục 5b/13.11): raw indicator value, tách khỏi
    # điểm số breakdown ở trên — dùng để train Entry Model và Feature Lineage sau này.
    raw = {
        "ema20": _safe_float(last.get("ema20")),
        "ema50": _safe_float(last.get("ema50")),
        "ema200": _safe_float(last.get("ema200")),
        "rsi": _safe_float(last.get("rsi")),
        "macd": _safe_float(last.get("macd")),
        "macd_signal": _safe_float(last.get("macd_signal")),
        "adx": _safe_float(last.get("adx")),
        "atr": _safe_float(last.get("atr")),
        "vwap": _safe_float(last.get("vwap")),
        "vol_sma20": _safe_float(last.get("vol_sma20")),
        "supertrend_dir": _safe_float(last.get("supertrend_dir")),
        "mtf_agreement_ratio": round(agreement_ratio, 3),
    }

    return {
        "total": total,
        "breakdown": {k: round(v, 2) for k, v in breakdown.items()},
        "raw": raw,
        "last_price": float(last["close"]),
    }


def compute_technical_score(df_by_tf: dict, primary_tf: str) -> dict:
    """df_by_tf: {timeframe: DataFrame OHLCV thô}. Trả về breakdown điểm 0-100."""
    enriched = {tf: add_indicators(df) for tf, df in df_by_tf.items()}
    primary = enriched[primary_tf]

    # Đồng thuận đa khung cho Trend/MACD
    directions = [_trend_direction(df) for df in enriched.values()]
    primary_dir = _trend_direction(primary)
    agree = sum(1 for d in directions if d == primary_dir and d != 0)
    agreement_ratio = agree / len(directions) if directions else 0

    return score_from_indicators(primary, idx=-1, agreement_ratio=agreement_ratio)


def _detect_bearish_pattern(df: pd.DataFrame, idx: int = -1) -> bool:
    """Bearish engulfing — mirror của `_detect_bullish_pattern`, dùng để thử
    nghiệm chiến lược Short riêng (xem docs/tasks.md, phát hiện "buy đỉnh cục
    bộ" từ AI Review Backtest). Chưa dùng trong Rule Engine live."""
    if len(df) < 2 or idx == 0:
        return False
    prev, last = df.iloc[idx - 1], df.iloc[idx]
    prev_bullish = prev["close"] > prev["open"]
    last_bearish = last["close"] < last["open"]
    engulfs = last["close"] <= prev["open"] and last["open"] >= prev["close"]
    return bool(prev_bullish and last_bearish and engulfs)


def score_short_from_indicators(primary_enriched: pd.DataFrame, idx: int = -1, agreement_ratio: float = 1.0) -> dict:
    """Mirror của `score_from_indicators` cho tín hiệu Short — cùng SCORE_WEIGHTS,
    đảo hướng từng điều kiện (bearish stack, MACD bearish cross, RSI đảo chiều
    từ overbought, Supertrend xuống, dưới VWAP, bearish engulfing).
    """
    last = primary_enriched.iloc[idx]
    breakdown = {}

    bearish_stack = last["ema20"] < last["ema50"] < last["ema200"] if not pd.isna(last["ema200"]) else False
    breakdown["ema_trend"] = SCORE_WEIGHTS["ema_trend"] * agreement_ratio if bearish_stack else 0

    macd_bearish_cross = False
    if len(primary_enriched) >= 2 and idx != 0:
        prev = primary_enriched.iloc[idx - 1]
        if not pd.isna(prev["macd"]) and not pd.isna(last["macd"]):
            macd_bearish_cross = prev["macd"] >= prev["macd_signal"] and last["macd"] < last["macd_signal"]
    breakdown["macd_cross"] = SCORE_WEIGHTS["macd_cross"] * agreement_ratio if macd_bearish_cross else 0

    rsi_topping = False
    if len(primary_enriched) >= 2 and idx != 0:
        prev_rsi, last_rsi = primary_enriched.iloc[idx - 1]["rsi"], last["rsi"]
        rsi_topping = not pd.isna(prev_rsi) and not pd.isna(last_rsi) and prev_rsi > 70 >= last_rsi
    breakdown["rsi_oversold"] = SCORE_WEIGHTS["rsi_oversold"] if rsi_topping else 0

    breakdown["adx_strong"] = SCORE_WEIGHTS["adx_strong"] if (not pd.isna(last["adx"]) and last["adx"] > 25) else 0

    vol_spike = not pd.isna(last["vol_sma20"]) and last["vol_sma20"] > 0 and last["volume"] > 1.5 * last["vol_sma20"]
    breakdown["volume_spike"] = SCORE_WEIGHTS["volume_spike"] if vol_spike else 0

    breakdown["supertrend"] = SCORE_WEIGHTS["supertrend"] if last["supertrend_dir"] == -1 else 0

    breakdown["vwap"] = SCORE_WEIGHTS["vwap"] if (not pd.isna(last["vwap"]) and last["close"] < last["vwap"]) else 0

    breakdown["pattern"] = SCORE_WEIGHTS["pattern"] if _detect_bearish_pattern(primary_enriched, idx) else 0

    total = round(sum(breakdown.values()), 2)
    return {
        "total": total,
        "breakdown": {k: round(v, 2) for k, v in breakdown.items()},
        "last_price": float(last["close"]),
    }


def _safe_float(x):
    return None if x is None or pd.isna(x) else float(x)
