"""Decision Engine: tổng hợp trọng số các lớp + state machine BUY/SELL.

Pipeline hiện tại:
Market Regime -> Sentiment Filter -> Order Flow Filter -> Technical Filter
-> Risk Engine -> BUY / HOLD / SELL
(Macro/News/On-chain Filter chưa triển khai)
"""
from datetime import datetime, timezone

import pandas as pd

from .. import config


def compute_total_score(layer_scores: dict) -> float:
    total = 0.0
    for layer, weight in config.WEIGHTS.items():
        total += layer_scores.get(layer, 50.0) * (weight / 100)
    return round(total, 2)


def decide_entry(
    total_score: float,
    regime_label: str,
    trading_halted: bool,
    kill_switch_on: bool = False,
    kill_switch_reason: str = "",
    cooldown_remaining_seconds: float = 0.0,
    buy_threshold: float | None = None,
    watch_threshold: float | None = None,
    pullback_ok: bool = True,
) -> tuple[str, str]:
    """`buy_threshold`/`watch_threshold` mặc định lấy từ config (live) — Backtest
    Engine có thể truyền riêng để thử calibrate ngưỡng trên dữ liệu lịch sử mà
    không ảnh hưởng ngưỡng đang chạy live.

    `pullback_ok` (pullback filter, `technical.pullback_ok`): gate cứng giống
    Regime — mặc định True để không phá vỡ caller cũ chưa truyền, nhưng khi
    caller truyền False (giá đang breakout/extended, chưa hồi về gần EMA20) thì
    chặn BUY bất kể `total_score`, tránh mua đúng lúc giá sắp đảo chiều.
    """
    buy_threshold = config.BUY_SCORE_THRESHOLD if buy_threshold is None else buy_threshold
    watch_threshold = config.WATCH_SCORE_THRESHOLD if watch_threshold is None else watch_threshold

    if kill_switch_on:
        return "IGNORE", f"Kill switch đang bật ({kill_switch_reason or 'không rõ lý do'}) — không vào lệnh mới"

    if cooldown_remaining_seconds > 0:
        return "IGNORE", f"Đang trong cooldown sau lệnh gần nhất, còn {round(cooldown_remaining_seconds)}s"

    if trading_halted:
        return "IGNORE", "Đã chạm daily loss limit — tạm dừng phát tín hiệu mới hôm nay"

    if regime_label in ("HIGH_VOLATILITY", "UNKNOWN"):
        return "IGNORE", f"Regime {regime_label} — rủi ro cao, không vào lệnh mới"

    if total_score >= buy_threshold and not pullback_ok:
        return "IGNORE", f"Tổng điểm {total_score} đạt ngưỡng BUY nhưng giá đang breakout/extended, chưa hồi về vùng pullback"

    if total_score >= buy_threshold:
        return "BUY", f"Tổng điểm {total_score} >= ngưỡng BUY {buy_threshold}"
    if total_score >= watch_threshold:
        return "WATCH", f"Tổng điểm {total_score} trong vùng theo dõi"
    return "IGNORE", f"Tổng điểm {total_score} dưới ngưỡng theo dõi"


def decide_exit(
    position_state: dict,
    current_price: float,
    df_indicators: pd.DataFrame,
    idx: int = -1,
    current_time=None,
    max_hold_minutes: float | None = None,
    min_hold_satisfied: bool | None = None,
) -> tuple[bool, str]:
    """Điều kiện thoát lệnh (state machine sau khi BUY).

    `idx` mặc định -1 (bar cuối, dùng cho live). Backtest Engine truyền `idx`
    cụ thể để đọc trực tiếp từ dataframe đã `add_indicators()` 1 lần, tránh
    phải cắt slice/tính lại indicator mỗi bar (xem `technical.score_from_indicators`).

    Không có minimum hold; tham số `min_hold_satisfied` chỉ còn để caller cũ
    không crash và bị bỏ qua. `current_time` cho backtest truyền timestamp của
    tick/bar; live mặc định dùng UTC now. Quá `max_hold_minutes` đóng bằng
    TIMEOUT_EXIT.
    """
    last = df_indicators.iloc[idx]
    stop_price = position_state["stop_price"]
    take_profit_price = position_state["take_profit_price"]

    if stop_price is not None and current_price <= stop_price:
        return True, f"Chạm stop loss ({current_price} <= {stop_price})"

    if take_profit_price is not None and current_price >= take_profit_price:
        return True, f"Đạt take profit ({current_price} >= {take_profit_price})"

    if _max_hold_reached(position_state.get("entry_time"), current_time, max_hold_minutes):
        return True, f"TIMEOUT_EXIT: giữ lệnh đủ {max_hold_minutes or config.MAX_HOLD_MINUTES:g} phút"

    if len(df_indicators) >= 2 and idx != 0:
        prev = df_indicators.iloc[idx - 1]
        macd_bearish_cross = (
            not pd.isna(prev.get("macd")) and not pd.isna(last.get("macd"))
            and prev["macd"] >= prev["macd_signal"] and last["macd"] < last["macd_signal"]
        )
        if macd_bearish_cross:
            return True, "MACD đảo chiều xuống"

    if not pd.isna(last.get("rsi")) and last["rsi"] > 75:
        return True, "RSI quá mua (>75)"

    # Volume giảm mạnh MỘT MÌNH không đủ để kết luận momentum yếu — nến vẫn xanh
    # (giá đi lên) lúc volume thấp chỉ là tạm nghỉ trong trend, không phải đảo
    # chiều (phát hiện: điều kiện chỉ-volume chiếm 55-71% tổng số lệnh thoát,
    # gần như luôn lỗ). Yêu cầu thêm nến đỏ tại thời điểm xét để xác nhận giá
    # cũng đang yếu đi.
    vol_weak = not pd.isna(last.get("vol_sma20")) and last["vol_sma20"] > 0 and last["volume"] < 0.5 * last["vol_sma20"]
    if vol_weak and last["close"] < last["open"]:
        return True, "Volume giảm mạnh kèm giá yếu, momentum suy giảm"

    if not pd.isna(last.get("ema20")) and not pd.isna(last.get("ema50")) and last["ema20"] < last["ema50"]:
        return True, "EMA20 cắt xuống EMA50"

    return False, ""


def decide_short_entry(total_score: float, regime_label: str, trading_halted: bool, **kwargs) -> tuple[str, str]:
    """Thử nghiệm chiến lược Short riêng (phát hiện "buy đỉnh cục bộ" từ AI
    Review Backtest) — tái dùng gating của `decide_entry`
    (kill switch/cooldown/trading halted/regime/ngưỡng), chỉ đổi nhãn BUY->SHORT.
    Chưa dùng trong Rule Engine live/`run.py`.
    """
    action, reason = decide_entry(total_score, regime_label, trading_halted, **kwargs)
    return ("SHORT", reason) if action == "BUY" else (action, reason)


def decide_short_exit(
    position_state: dict,
    current_price: float,
    df_indicators: pd.DataFrame,
    idx: int = -1,
    current_time=None,
    max_hold_minutes: float | None = None,
    min_hold_satisfied: bool | None = None,
) -> tuple[bool, str]:
    """Mirror của `decide_exit` cho lệnh Short — dùng để thử nghiệm chiến lược
    Short riêng, chưa dùng trong Rule Engine live/`run.py`."""
    last = df_indicators.iloc[idx]
    stop_price = position_state["stop_price"]
    take_profit_price = position_state["take_profit_price"]

    if stop_price is not None and current_price >= stop_price:
        return True, f"Chạm stop loss short ({current_price} >= {stop_price})"

    if take_profit_price is not None and current_price <= take_profit_price:
        return True, f"Đạt take profit short ({current_price} <= {take_profit_price})"

    if _max_hold_reached(position_state.get("entry_time"), current_time, max_hold_minutes):
        return True, f"TIMEOUT_EXIT: giữ lệnh đủ {max_hold_minutes or config.MAX_HOLD_MINUTES:g} phút"

    if len(df_indicators) >= 2 and idx != 0:
        prev = df_indicators.iloc[idx - 1]
        macd_bullish_cross = (
            not pd.isna(prev.get("macd")) and not pd.isna(last.get("macd"))
            and prev["macd"] <= prev["macd_signal"] and last["macd"] > last["macd_signal"]
        )
        if macd_bullish_cross:
            return True, "MACD đảo chiều lên"

    if not pd.isna(last.get("rsi")) and last["rsi"] < 25:
        return True, "RSI quá bán (<25)"

    # Mirror của decide_exit — xem comment ở đó (2026-08-05): yêu cầu thêm nến xanh
    # (giá hồi lên) tại thời điểm xét để xác nhận momentum giảm thật, không chỉ volume.
    vol_weak = not pd.isna(last.get("vol_sma20")) and last["vol_sma20"] > 0 and last["volume"] < 0.5 * last["vol_sma20"]
    if vol_weak and last["close"] > last["open"]:
        return True, "Volume giảm mạnh kèm giá hồi, momentum suy giảm"

    if not pd.isna(last.get("ema20")) and not pd.isna(last.get("ema50")) and last["ema20"] > last["ema50"]:
        return True, "EMA20 cắt lên EMA50"

    return False, ""


def decide_support_resistance_entry(buy_score: float, sell_score: float,
                                    threshold: float | None = None,
                                    buy_eligible: bool = True,
                                    ineligible_reason: str | None = None) -> tuple[str, str]:
    """Decision thuần S/R; không áp dụng regime/pullback hay layer khác."""
    threshold = config.SR_DECISION_THRESHOLD if threshold is None else threshold
    if buy_score >= threshold and sell_score >= threshold:
        return "HOLD", "Support và Resistance đồng thời đủ điểm — vùng quá hẹp/xung đột"
    if not buy_eligible:
        return "IGNORE", f"S/R chưa đủ điều kiện BUY ({ineligible_reason or 'không rõ'}) — observation score {buy_score}"
    if buy_score >= threshold:
        return "BUY", f"Support score {buy_score} >= {threshold}"
    if buy_score >= config.WATCH_SCORE_THRESHOLD:
        return "WATCH", f"Support score {buy_score} trong vùng theo dõi"
    return "IGNORE", f"Support score {buy_score} dưới ngưỡng {threshold}"


def decide_support_resistance_exit(position_state: dict, current_price: float,
                                   sell_score: float, current_time=None,
                                   threshold: float | None = None) -> tuple[bool, str]:
    """Exit Long bằng SL/TP/timeout hoặc SELL score phá support."""
    threshold = config.SR_DECISION_THRESHOLD if threshold is None else threshold
    stop_price = position_state.get("stop_price")
    take_profit_price = position_state.get("take_profit_price")
    if stop_price is not None and current_price <= stop_price:
        return True, f"STOP_LOSS ({current_price} <= {stop_price})"
    if take_profit_price is not None and current_price >= take_profit_price:
        tp_reason = position_state.get("tp_reason") or "TAKE_PROFIT"
        return True, f"{tp_reason} ({current_price} >= {take_profit_price})"
    if _max_hold_reached(position_state.get("entry_time"), current_time):
        return True, f"TIMEOUT_EXIT: giữ lệnh đủ {config.MAX_HOLD_MINUTES:g} phút"
    if sell_score >= threshold:
        return True, f"SELL_SCORE: support breakdown score {sell_score} >= {threshold}"
    return False, ""


def _utc_datetime(value) -> datetime:
    """Chuẩn hóa ISO string/datetime/pandas Timestamp về UTC aware datetime."""
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    elif hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _max_hold_reached(entry_time, current_time=None, max_hold_minutes: float | None = None) -> bool:
    if entry_time is None:
        return False
    limit = config.MAX_HOLD_MINUTES if max_hold_minutes is None else max_hold_minutes
    return limit > 0 and holding_minutes(entry_time, current_time) >= limit
