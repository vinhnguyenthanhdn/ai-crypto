"""Order Flow score — nhóm Order Book & Order Flow.

Bid/Ask Imbalance vẫn lấy từ REST order book snapshot mỗi lần chạy. CVD ưu
tiên lấy từ trade stream WS thật (`collector_ws.py`, qua `state_store.get_ws_cvd`)
khi có sẵn — chính xác hơn REST snapshot vì không bỏ sót trade giữa các lần
poll. Fallback về CVD xấp xỉ từ REST snapshot trades nếu collector_ws chưa
chạy/dữ liệu quá cũ.
"""


def _clip(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def _to_0_100(ratio):
    return round((_clip(ratio) + 1) / 2 * 100, 2)


def bid_ask_imbalance(order_book, depth=20):
    bids = order_book.get("bids", [])[:depth]
    asks = order_book.get("asks", [])[:depth]
    bid_vol = sum(b[1] for b in bids)
    ask_vol = sum(a[1] for a in asks)
    total = bid_vol + ask_vol
    if total == 0:
        return 0.0
    return (bid_vol - ask_vol) / total


def cumulative_volume_delta(trades):
    """CVD xấp xỉ từ trade side (taker buy vs taker sell) — REST snapshot,
    dùng làm fallback khi không có CVD từ WS (xem docstring module)."""
    buy_vol = sum(t["amount"] for t in trades if t.get("side") == "buy")
    sell_vol = sum(t["amount"] for t in trades if t.get("side") == "sell")
    total = buy_vol + sell_vol
    if total == 0:
        return 0.0
    return (buy_vol - sell_vol) / total


def compute_order_flow_score(order_book, trades, ws_cvd: float | None = None) -> dict:
    imbalance = bid_ask_imbalance(order_book)
    cvd = ws_cvd if ws_cvd is not None else cumulative_volume_delta(trades)
    cvd_source = "websocket" if ws_cvd is not None else "rest_snapshot"

    imbalance_score = _to_0_100(imbalance)
    cvd_score = _to_0_100(cvd)
    total = round((imbalance_score + cvd_score) / 2, 2)

    return {
        "total": total,
        "breakdown": {
            "bid_ask_imbalance": imbalance_score,
            "cvd": cvd_score,
        },
        "raw": {"imbalance": round(imbalance, 4), "cvd": round(cvd, 4), "cvd_source": cvd_source},
    }
