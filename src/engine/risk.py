"""Risk Engine (xem plan-02.md, rủi ro "Không có position sizing / daily loss limit").

Position size theo % vốn cố định + khoảng cách stop dựa trên ATR, không theo
cảm tính. Take-profit dùng risk:reward cố định 1.5:1.

**Cost gate** (xem docs/research-technical-signal-edge.md mục 6.1): khoảng cách
TP/SL thuần theo ATR không tự động lớn hơn chi phí giao dịch. Trên khung nhiễu
(ATR nhỏ so với giá), TP có thể nằm GẦN entry hơn cả chi phí khứ hồi — lệnh chạy
đúng kịch bản tốt nhất vẫn lỗ. `compute_position_plan` vì vậy trả thêm
`edge_viable`: False nghĩa là cấu trúc lệnh này không thể có lãi kể cả khi đúng
hướng, phải bỏ qua entry chứ không phải chấp nhận rủi ro thấp.
"""
from .. import config

RISK_REWARD_RATIO = config.RISK_REWARD_RATIO
ATR_STOP_MULTIPLIER = config.ATR_STOP_MULTIPLIER


def round_trip_cost_pct(fee_pct: float | None = None, slippage_pct: float | None = None) -> float:
    """Chi phí khứ hồi tính theo % giá (vào + ra, mỗi chiều gồm fee và slippage)."""
    fee_pct = config.FEE_PCT if fee_pct is None else fee_pct
    slippage_pct = config.SLIPPAGE_PCT if slippage_pct is None else slippage_pct
    return (fee_pct + slippage_pct) * 2 * 100


def _plan_common(entry_price: float, atr: float, stop_price: float, take_profit_price: float,
                 fee_pct: float | None, slippage_pct: float | None) -> dict:
    stop_distance = atr * ATR_STOP_MULTIPLIER
    cost_pct = round_trip_cost_pct(fee_pct, slippage_pct)
    tp_distance_pct = abs(take_profit_price - entry_price) / entry_price * 100 if entry_price else 0.0

    risk_amount_usd = config.ACCOUNT_EQUITY_USD * (config.RISK_PER_TRADE_PCT / 100)
    size_usd = 0.0
    if stop_distance > 0:
        size_units = risk_amount_usd / stop_distance
        size_usd = round(size_units * entry_price, 2)
        # không vượt quá toàn bộ vốn tài khoản dù ATR quá nhỏ
        size_usd = min(size_usd, config.ACCOUNT_EQUITY_USD)

    edge_viable = tp_distance_pct >= cost_pct * config.MIN_TP_COST_RATIO
    return {
        "stop_price": round(stop_price, 2),
        "take_profit_price": round(take_profit_price, 2),
        "size_usd": size_usd,
        "risk_amount_usd": round(risk_amount_usd, 2),
        "tp_distance_pct": round(tp_distance_pct, 4),
        "sl_distance_pct": round(stop_distance / entry_price * 100, 4) if entry_price else 0.0,
        "round_trip_cost_pct": round(cost_pct, 4),
        "min_tp_distance_pct": round(cost_pct * config.MIN_TP_COST_RATIO, 4),
        "edge_viable": edge_viable,
        "skip_reason": "" if edge_viable else (
            f"TP cách entry {tp_distance_pct:.3f}% < mức tối thiểu "
            f"{cost_pct * config.MIN_TP_COST_RATIO:.3f}% (chi phí khứ hồi {cost_pct:.3f}% "
            f"x {config.MIN_TP_COST_RATIO}) — biến động quá nhỏ so với chi phí"
        ),
    }


def compute_position_plan(entry_price: float, atr: float,
                          fee_pct: float | None = None, slippage_pct: float | None = None):
    stop_distance = atr * ATR_STOP_MULTIPLIER
    return _plan_common(
        entry_price, atr,
        stop_price=entry_price - stop_distance,
        take_profit_price=entry_price + stop_distance * RISK_REWARD_RATIO,
        fee_pct=fee_pct, slippage_pct=slippage_pct,
    )


def compute_pnl_pct(entry_price: float, exit_price: float) -> float:
    return round((exit_price - entry_price) / entry_price * 100, 3)


def compute_short_position_plan(entry_price: float, atr: float,
                                fee_pct: float | None = None, slippage_pct: float | None = None):
    """Mirror của `compute_position_plan` cho lệnh Short — dùng để thử nghiệm
    chiến lược Short riêng (xem docs/tasks.md, phát hiện "buy đỉnh cục bộ" từ
    AI Review Backtest). Chưa dùng trong Rule Engine live/`run.py`."""
    stop_distance = atr * ATR_STOP_MULTIPLIER
    return _plan_common(
        entry_price, atr,
        stop_price=entry_price + stop_distance,
        take_profit_price=entry_price - stop_distance * RISK_REWARD_RATIO,
        fee_pct=fee_pct, slippage_pct=slippage_pct,
    )


def compute_short_pnl_pct(entry_price: float, exit_price: float) -> float:
    return round((entry_price - exit_price) / entry_price * 100, 3)
