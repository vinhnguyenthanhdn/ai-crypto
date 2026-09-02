"""Risk Engine: chống rủi ro "không có position sizing / daily loss limit".

Position size theo % vốn cố định + khoảng cách stop dựa trên ATR, không theo
cảm tính. Take-profit dùng risk:reward cố định 1.5:1.

**Cost gate:** khoảng cách TP/SL thuần theo ATR không tự động lớn hơn chi phí
giao dịch. Trên khung nhiễu
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


def compute_liquidation_price(entry_price: float, leverage: float, side: str = "long",
                              maintenance_margin_rate: float = 0.005) -> float:
    """Giá thanh lý xấp xỉ (isolated margin, bỏ qua funding) — chỉ có nghĩa khi
    `MARKET_TYPE=swap` với `leverage > 1` (xem `TODO-SWAP-PARITY`); ở Spot/1x, trả về giá
    trị không bao giờ chạm được (0 cho long, inf cho short) để các hàm dùng
    chung không cần rẽ nhánh riêng theo market type."""
    if leverage <= 1:
        return 0.0 if side == "long" else float("inf")
    if side == "long":
        return entry_price * (1 - 1 / leverage + maintenance_margin_rate)
    return entry_price * (1 + 1 / leverage - maintenance_margin_rate)


def sl_beyond_liquidation(stop_price: float, liquidation_price: float, side: str = "long") -> bool:
    """True nghĩa là SL đặt SAU điểm thanh lý — tài khoản bị thanh lý trước khi
    SL kịp kích hoạt, kế hoạch lệnh này vô nghĩa ở mức leverage đang dùng."""
    if side == "long":
        return stop_price <= liquidation_price
    return stop_price >= liquidation_price


def max_concurrent_positions(scoring_profile: str | None = None) -> int:
    """Trần số lệnh song song, và là nguồn duy nhất cho nó.

    Profile `support_resistance_only` chốt ở một slot bất kể cấu hình: nó vào
    lệnh theo một vùng support cụ thể, nên lệnh thứ hai cùng lúc là cùng một
    luận điểm đặt hai lần chứ không phải hai luận điểm.

    Trần này có **hai** người đọc mang hai nghĩa khác nhau — cổng đếm ở
    `run.py:_handle_entry` và ngân sách rủi ro danh mục ở `_plan_common` — nên
    nó phải nằm ở đúng một chỗ. Trước đó cả hai điểm quyết định trong `run.py`
    tự viết lại cùng một biểu thức, còn ngân sách rủi ro đọc thẳng
    `config.MAX_CONCURRENT_POSITIONS` và **bỏ qua vế profile**: dưới profile SR
    với cấu hình > 1, cổng đếm cho một slot trong khi ngân sách vẫn cấp cho
    nhiều, tức lớp ngân sách âm thầm thôi ràng buộc gì.
    """
    profile = config.SCORING_PROFILE if scoring_profile is None else scoring_profile
    if profile == "support_resistance_only":
        return 1
    return int(config.MAX_CONCURRENT_POSITIONS)


def compute_open_risk_usd(open_positions: list) -> float:
    """Tổng USD sẽ mất nếu TẤT CẢ vị thế đang mở đều bị chạm Stop Loss — ngân
    sách rủi ro danh mục đã bị các lệnh đang mở chiếm dụng, dùng để xét thêm 1
    lệnh mới có vượt tổng rủi ro cho phép không (không chỉ xét riêng lệnh mới)."""
    total = 0.0
    for p in open_positions:
        entry_price = p.get("entry_price")
        stop_price = p.get("stop_price")
        size_usd = p.get("size_usd")
        if not entry_price or stop_price is None or not size_usd:
            continue
        stop_distance_pct = abs(entry_price - stop_price) / entry_price
        total += size_usd * stop_distance_pct
    return round(total, 2)


def _plan_common(entry_price: float, stop_price: float, take_profit_price: float,
                 fee_pct: float | None, slippage_pct: float | None,
                 already_committed_risk_usd: float = 0.0, side: str = "long",
                 account_equity_usd: float | None = None) -> dict:
    """`stop_distance` luôn suy từ khoảng cách entry-stop THẬT (không phải suy
    ngược từ ATR) — để Position Sizing đúng ngay cả khi `stop_price` đến từ
    cấu trúc giá (swing low/support, xem `compute_structural_position_plan`)
    chứ không phải thuần ATR."""
    stop_distance = abs(entry_price - stop_price)
    cost_pct = round_trip_cost_pct(fee_pct, slippage_pct)
    tp_distance_pct = abs(take_profit_price - entry_price) / entry_price * 100 if entry_price else 0.0

    # Chỉ có ý nghĩa khi MARKET_TYPE=swap + LEVERAGE>1 (compute_liquidation_price
    # trả 0/inf ở Spot/1x nên check này luôn no-op, không đổi hành vi Spot).
    liquidation_price = compute_liquidation_price(entry_price, config.LEVERAGE, side=side)
    sl_invalid = sl_beyond_liquidation(stop_price, liquidation_price, side=side)

    account_equity_usd = config.ACCOUNT_EQUITY_USD if account_equity_usd is None else account_equity_usd
    risk_amount_usd = account_equity_usd * (config.RISK_PER_TRADE_PCT / 100)
    # Ngân sách rủi ro toàn danh mục = rủi ro/lệnh x số lệnh song song tối đa —
    # lệnh mới không được đẩy tổng rủi ro (đang mở + lệnh này) vượt mức này,
    # dù từng lệnh riêng lẻ vẫn size đúng theo ATR/% vốn như cũ.
    portfolio_risk_budget_usd = risk_amount_usd * max_concurrent_positions()
    remaining_risk_budget_usd = max(0.0, portfolio_risk_budget_usd - already_committed_risk_usd)
    risk_amount_usd = min(risk_amount_usd, remaining_risk_budget_usd)

    size_usd = 0.0
    if stop_distance > 0 and risk_amount_usd > 0:
        size_units = risk_amount_usd / stop_distance
        size_usd = round(size_units * entry_price, 2)
        # không vượt quá toàn bộ vốn tài khoản dù ATR quá nhỏ
        size_usd = min(size_usd, account_equity_usd)

    edge_viable = tp_distance_pct >= cost_pct * config.MIN_TP_COST_RATIO
    if edge_viable and sl_invalid:
        edge_viable = False
        skip_reason = (
            f"SL ({stop_price}) nằm sau điểm thanh lý ({round(liquidation_price, 2)}) ở leverage "
            f"{config.LEVERAGE}x — tài khoản bị thanh lý trước khi SL kịp kích hoạt"
        )
    elif edge_viable and size_usd <= 0:
        edge_viable = False
        skip_reason = (
            f"Ngân sách rủi ro danh mục đã dùng hết bởi vị thế đang mở "
            f"(đã chiếm ${already_committed_risk_usd}/${round(portfolio_risk_budget_usd, 2)})"
        )
    else:
        skip_reason = "" if edge_viable else (
            f"TP cách entry {tp_distance_pct:.3f}% < mức tối thiểu "
            f"{cost_pct * config.MIN_TP_COST_RATIO:.3f}% (chi phí khứ hồi {cost_pct:.3f}% "
            f"x {config.MIN_TP_COST_RATIO}) — biến động quá nhỏ so với chi phí"
        )
    return {
        "stop_price": round(stop_price, 2),
        "take_profit_price": round(take_profit_price, 2),
        "size_usd": size_usd,
        "risk_amount_usd": round(risk_amount_usd, 2),
        "account_equity_usd": round(account_equity_usd, 2),
        "tp_distance_pct": round(tp_distance_pct, 4),
        "sl_distance_pct": round(stop_distance / entry_price * 100, 4) if entry_price else 0.0,
        "round_trip_cost_pct": round(cost_pct, 4),
        "min_tp_distance_pct": round(cost_pct * config.MIN_TP_COST_RATIO, 4),
        "liquidation_price": round(liquidation_price, 2) if liquidation_price not in (0.0, float("inf")) else None,
        "edge_viable": edge_viable,
        "skip_reason": skip_reason,
    }


def compute_position_plan(entry_price: float, atr: float,
                          fee_pct: float | None = None, slippage_pct: float | None = None,
                          already_committed_risk_usd: float = 0.0,
                          account_equity_usd: float | None = None):
    stop_distance = atr * ATR_STOP_MULTIPLIER
    return _plan_common(
        entry_price,
        stop_price=entry_price - stop_distance,
        take_profit_price=entry_price + stop_distance * RISK_REWARD_RATIO,
        fee_pct=fee_pct, slippage_pct=slippage_pct,
        already_committed_risk_usd=already_committed_risk_usd, side="long",
        account_equity_usd=account_equity_usd,
    )


def compute_pnl_pct(entry_price: float, exit_price: float) -> float:
    return round((exit_price - entry_price) / entry_price * 100, 3)


def compute_short_position_plan(entry_price: float, atr: float,
                                fee_pct: float | None = None, slippage_pct: float | None = None,
                                already_committed_risk_usd: float = 0.0,
                                account_equity_usd: float | None = None):
    """Mirror của `compute_position_plan` cho lệnh Short — dùng để thử nghiệm
    chiến lược Short riêng (phát hiện "buy đỉnh cục bộ" từ AI Review Backtest).
    Chưa dùng trong Rule Engine live/`run.py`."""
    stop_distance = atr * ATR_STOP_MULTIPLIER
    return _plan_common(
        entry_price,
        stop_price=entry_price + stop_distance,
        take_profit_price=entry_price - stop_distance * RISK_REWARD_RATIO,
        fee_pct=fee_pct, slippage_pct=slippage_pct,
        already_committed_risk_usd=already_committed_risk_usd, side="short",
        account_equity_usd=account_equity_usd,
    )


def compute_short_pnl_pct(entry_price: float, exit_price: float) -> float:
    return round((entry_price - exit_price) / entry_price * 100, 3)


def compute_structural_position_plan(entry_price: float, atr: float, swing_low: float | None,
                                     swing_high: float | None = None,
                                     atr_buffer_mult: float = 0.5,
                                     fee_pct: float | None = None, slippage_pct: float | None = None,
                                     already_committed_risk_usd: float = 0.0,
                                     account_equity_usd: float | None = None) -> dict:
    """SL/TP theo cấu trúc giá (`TODO-MTF-CONFLUENCE`) — thử nghiệm, CHƯA dùng trong
    Rule Engine live/`run.py`, chỉ dùng để backtest so sánh với
    `compute_position_plan` (thuần ATR) trước khi quyết định đổi mặc định.

    SL đặt dưới `swing_low` (điểm invalidation tự nhiên của thesis long) kèm
    buffer `atr_buffer_mult * atr` chống stop-hunt bởi wick. TP nhắm
    `swing_high` (resistance/previous high gần nhất) nếu có và nằm trên entry;
    nếu không có/không hợp lệ, fallback về TP theo ATR*R:R như cũ (không thể
    tính TP structural nếu chưa xác định được resistance phía trên).

    `swing_low`/`swing_high`: lấy từ `technical.find_recent_swing_low/high`.
    """
    if swing_low is None or swing_low >= entry_price:
        return compute_position_plan(
            entry_price, atr, fee_pct, slippage_pct, already_committed_risk_usd,
            account_equity_usd,
        )

    stop_price = swing_low - atr_buffer_mult * atr
    if swing_high is not None and swing_high > entry_price:
        take_profit_price = swing_high
    else:
        stop_distance = atr * ATR_STOP_MULTIPLIER
        take_profit_price = entry_price + stop_distance * RISK_REWARD_RATIO

    return _plan_common(
        entry_price, stop_price=stop_price, take_profit_price=take_profit_price,
        fee_pct=fee_pct, slippage_pct=slippage_pct,
        already_committed_risk_usd=already_committed_risk_usd, side="long",
        account_equity_usd=account_equity_usd,
    )


def compute_trade_accounting(
    entry_market_price: float,
    exit_market_price: float,
    size_usd: float,
    *,
    side: str = "long",
    fee_pct: float | None = None,
    slippage_pct: float | None = None,
    funding_cost_pct: float = 0.0,
    equity_before_usd: float | None = None,
) -> dict:
    """Accounting chung cho live/backtest từ giá market thô.

    Slippage được áp dụng ở cả entry và exit; fee tính trên notional fill mỗi
    chiều. `funding_cost_pct` dương nghĩa là strategy trả funding, âm là nhận.
    """
    if entry_market_price <= 0 or exit_market_price <= 0 or size_usd <= 0:
        raise ValueError("Giá entry/exit và size_usd phải dương")
    if side not in ("long", "short"):
        raise ValueError("side phải là long hoặc short")
    fee_pct = config.FEE_PCT if fee_pct is None else fee_pct
    slippage_pct = config.SLIPPAGE_PCT if slippage_pct is None else slippage_pct
    equity_before_usd = config.ACCOUNT_EQUITY_USD if equity_before_usd is None else equity_before_usd

    if side == "long":
        entry_fill = entry_market_price * (1 + slippage_pct)
        exit_fill = exit_market_price * (1 - slippage_pct)
        gross_direction = exit_market_price - entry_market_price
        fill_direction = exit_fill - entry_fill
    else:
        entry_fill = entry_market_price * (1 - slippage_pct)
        exit_fill = exit_market_price * (1 + slippage_pct)
        gross_direction = entry_market_price - exit_market_price
        fill_direction = entry_fill - exit_fill

    units = size_usd / entry_fill
    entry_fee_usd = size_usd * fee_pct
    exit_notional_usd = units * exit_fill
    exit_fee_usd = exit_notional_usd * fee_pct
    gross_pnl_usd = units * gross_direction
    pnl_after_slippage_usd = units * fill_direction
    funding_cost_usd = size_usd * funding_cost_pct / 100
    net_pnl_usd = pnl_after_slippage_usd - entry_fee_usd - exit_fee_usd - funding_cost_usd
    equity_after_usd = equity_before_usd + net_pnl_usd

    def _pct(value, base):
        return value / base * 100 if base else 0.0

    return {
        "side": side,
        "size_usd": round(size_usd, 6),
        "units": round(units, 12),
        "entry_market_price": round(entry_market_price, 8),
        "exit_market_price": round(exit_market_price, 8),
        "entry_fill_price": round(entry_fill, 8),
        "exit_fill_price": round(exit_fill, 8),
        "entry_fee_usd": round(entry_fee_usd, 8),
        "exit_fee_usd": round(exit_fee_usd, 8),
        "funding_cost_usd": round(funding_cost_usd, 8),
        "gross_pnl_usd": round(gross_pnl_usd, 8),
        "net_pnl_usd": round(net_pnl_usd, 8),
        "gross_pnl_pct": round(_pct(gross_pnl_usd, size_usd), 6),
        "net_pnl_pct": round(_pct(net_pnl_usd, size_usd), 6),
        "return_on_equity_pct": round(_pct(net_pnl_usd, equity_before_usd), 6),
        "equity_before_usd": round(equity_before_usd, 8),
        "equity_after_usd": round(equity_after_usd, 8),
        "fee_pct": fee_pct,
        "slippage_pct": slippage_pct,
        "funding_cost_pct": funding_cost_pct,
    }
