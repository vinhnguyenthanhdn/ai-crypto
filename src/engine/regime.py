"""Market Regime — rule-based, không dùng LLM (xem plan-02.md, góc nhìn bổ sung
"Market Regime nên rule-based, không phải AI").
"""
import pandas as pd


def classify_regime(df_with_indicators: pd.DataFrame, idx: int = -1) -> dict:
    last = df_with_indicators.iloc[idx]
    adx = last.get("adx")
    atr = last.get("atr")
    close = last.get("close")

    if pd.isna(adx) or pd.isna(atr) or pd.isna(close) or close == 0:
        return {"label": "UNKNOWN", "score": 50.0, "raw": {"adx": None, "atr_pct": None}}

    atr_pct = atr / close * 100

    if adx > 25 and atr_pct < 3:
        label, score = "STRONG_TREND", 90.0
    elif adx > 25 and atr_pct >= 3:
        label, score = "HIGH_VOLATILITY_TREND", 60.0
    elif 15 <= adx <= 25:
        label, score = "WEAK_TREND", 55.0
    elif atr_pct >= 4:
        label, score = "HIGH_VOLATILITY", 25.0
    else:
        label, score = "SIDEWAY", 35.0

    raw = {"adx": round(float(adx), 2), "atr_pct": round(float(atr_pct), 3)}
    return {"label": label, "score": score, "raw": raw, **raw}
