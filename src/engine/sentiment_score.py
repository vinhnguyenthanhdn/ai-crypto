"""Sentiment score — rule-based theo Fear & Greed Index (nhóm Market Sentiment).
Dùng logic contrarian: sợ hãi cực độ thường đi kèm khả năng bật lại ngắn hạn,
tham lam cực độ tăng rủi ro điều chỉnh.
"""


def compute_sentiment_score(fear_greed: dict | None) -> dict:
    if not fear_greed:
        return {"total": 50.0, "breakdown": {"fear_greed": None}, "raw": {"fear_greed_value": None}}

    value = fear_greed["value"]
    if value <= 25:
        score = 70.0
    elif value <= 45:
        score = 55.0
    elif value <= 55:
        score = 50.0
    elif value <= 75:
        score = 45.0
    else:
        score = 30.0

    raw = {"fear_greed_value": value, "classification": fear_greed["classification"]}
    return {"total": score, "breakdown": raw, "raw": raw}
