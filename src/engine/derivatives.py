"""Derivatives score (xem plan-02.md, nhóm Derivatives, 21%): Funding Rate + Open Interest.

Long/Short Ratio và Liquidation Heatmap chưa đưa vào scope (xem plan-02.md, mục
"Đã cắt khỏi scope") vì cần nguồn dữ liệu trả phí (Coinglass...).

OI không có sẵn % thay đổi theo API — lưu giá trị lần chạy trước vào SQLite
(kv_store) để tự tính xu hướng tăng/giảm giữa các lần cron gọi.
"""
from .. import state_store


def _funding_score(funding_rate):
    """Funding rate quá dương → thị trường long quá nóng (bearish); quá âm → short
    đang trả phí cho long, thường đi kèm khả năng bật lại (bullish).

    Ngoài mức tĩnh, còn bắt tín hiệu **đảo chiều** (funding flip dấu) — theo mục
    7b, đây là phần dự báo mạnh nhất của funding rate (flip dương→âm thường xảy
    ra gần đỉnh/đáy cục bộ), mạnh hơn cả mức tuyệt đối tại 1 thời điểm.
    """
    if funding_rate is None:
        return 50.0
    # funding rate thường ở thang %/8h, vd 0.0001 = 0.01%
    pct = funding_rate * 100

    prev_pct_raw = state_store.get_kv("last_funding_pct")
    state_store.set_kv("last_funding_pct", pct)
    prev_pct = float(prev_pct_raw) if prev_pct_raw is not None else None

    if prev_pct is not None:
        if prev_pct > 0 and pct <= 0:
            return 95.0  # flip dương -> âm/0: long quá nóng vừa hạ nhiệt, thường gần đáy cục bộ
        if prev_pct < 0 and pct >= 0:
            return 15.0  # flip âm -> dương/0: short covering vừa hết, thường gần đỉnh cục bộ

    if pct <= -0.02:
        return 90.0
    if pct <= 0:
        return 65.0
    if pct <= 0.02:
        return 50.0
    if pct <= 0.05:
        return 30.0
    return 10.0


def _oi_trend_score(current_oi, current_price):
    if current_oi is None or current_price is None:
        return 50.0

    prev_oi = state_store.get_kv("last_oi")
    prev_price = state_store.get_kv("last_price_for_oi")
    state_store.set_kv("last_oi", current_oi)
    state_store.set_kv("last_price_for_oi", current_price)

    if prev_oi is None or prev_price is None:
        return 50.0

    prev_oi, prev_price = float(prev_oi), float(prev_price)
    oi_up = current_oi > prev_oi
    price_up = current_price > prev_price

    if price_up and oi_up:
        return 85.0  # dòng tiền mới vào theo hướng tăng
    if price_up and not oi_up:
        return 45.0  # short covering, ít bền vững
    if not price_up and oi_up:
        return 20.0  # short mới vào, áp lực giảm tiếp
    return 50.0  # giá giảm + OI giảm: long đóng vị thế, trung tính


def compute_derivatives_score(funding_rate, open_interest, current_price) -> dict:
    funding_score = _funding_score(funding_rate)
    oi_score = _oi_trend_score(open_interest, current_price)
    total = round((funding_score + oi_score) / 2, 2)
    return {
        "total": total,
        "breakdown": {"funding": funding_score, "open_interest_trend": oi_score},
        "raw": {"funding_rate": funding_rate, "open_interest": open_interest},
    }
