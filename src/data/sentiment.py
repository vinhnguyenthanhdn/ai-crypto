"""Fear & Greed Index (alternative.me) — nhóm Market Sentiment."""
import requests

FNG_URL = "https://api.alternative.me/fng/?limit=1"


def fetch_fear_greed():
    try:
        resp = requests.get(FNG_URL, timeout=8)
        resp.raise_for_status()
        data = resp.json()["data"][0]
        return {"value": int(data["value"]), "classification": data["value_classification"]}
    except Exception:
        return None
