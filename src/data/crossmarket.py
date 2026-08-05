"""Cross-market context: Nasdaq, Gold, DXY, VIX (xem plan-02.md, nhóm Cross-market).

Dùng % thay đổi phiên gần nhất làm tín hiệu risk-on/risk-off đơn giản, không
cần độ trễ thấp vì đây là lớp bối cảnh, không phải lớp quyết định chính.
"""
import math

import yfinance as yf

TICKERS = {
    "nasdaq": "^IXIC",
    "gold": "GC=F",
    "dxy": "DX-Y.NYB",
    "vix": "^VIX",
}


def fetch_cross_market_changes():
    changes = {}
    for name, ticker in TICKERS.items():
        try:
            hist = yf.Ticker(ticker).history(period="5d", interval="1d")
            if len(hist) >= 2:
                prev, last = hist["Close"].iloc[-2], hist["Close"].iloc[-1]
                pct = (last - prev) / prev * 100
                changes[name] = None if math.isnan(pct) else round(float(pct), 3)
            else:
                changes[name] = None
        except Exception:
            changes[name] = None
    return changes
