"""Cross-market score — tương quan risk-on/risk-off đơn giản (xem plan-02.md,
mục 7b, nhóm Cross-market, 7%).

Hệ số lấy từ `config.CROSSMARKET_*_COEF`, không hard-code trong hàm — tương
quan thị trường đổi theo thời gian (vd BTC-DXY đã đảo chiều từ nghịch sang
thuận từ ~đầu 2026), cần cập nhật hệ số định kỳ mà không sửa logic ở đây.
"""
from .. import config


def _clip(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def compute_cross_market_score(changes: dict) -> dict:
    nasdaq = changes.get("nasdaq") or 0
    dxy = changes.get("dxy") or 0
    vix = changes.get("vix") or 0

    score = (
        50
        + nasdaq * config.CROSSMARKET_NASDAQ_COEF
        + dxy * config.CROSSMARKET_DXY_COEF
        + vix * config.CROSSMARKET_VIX_COEF
    )
    score = round(_clip(score), 2)

    return {"total": score, "breakdown": changes, "raw": changes}
