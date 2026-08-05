"""Backtest Engine (xem plan-02.md mục 11): replay Decision Engine + Risk Engine
trên OHLCV lịch sử, bar-by-bar, không look-ahead — quyết định ở bar `i` chỉ dùng
dữ liệu tới bar `i`, fill lệnh ở giá open bar `i+1` (không dùng close bar hiện tại
để vào lệnh, tránh look-ahead bias). Mô phỏng fee + slippage.

**Lưu ý fill SL/TP:** điều kiện chạm stop/take-profit được kiểm tra trên giá
close bar `i`, nhưng lệnh chỉ khớp ở giá open bar `i+1` (không giả định khớp
đúng giá SL/TP) — nên một lệnh "đạt take profit" vẫn có thể ra PnL âm nếu giá
hồi lại trước khi bar kế tiếp mở, cộng thêm phí. Đây là hành vi thiết kế
(tránh look-ahead), không phải lỗi.

**Không dùng vectorbt** dù đã cân nhắc ở mục 5c: vectorbt 1.1.0 yêu cầu
`pandas>=3.0.3`, trong khi MLflow (mục 5c, dùng cho Experiment Engine + Model
Registry) yêu cầu `pandas<3` — hai thư viện xung đột cứng, không cài chung được
trong 1 venv. Vì MLflow phục vụ nhiều task hơn (Experiment Engine + Champion-
Challenger), giữ MLflow và tự tính Drawdown/Sharpe/Win Rate (vài dòng, không
phức tạp) thay vì vectorbt. Xem plan-02.md mục 5c để biết cập nhật quyết định.

**Giới hạn đã biết:** chỉ replay được lớp Technical + Market Regime — 2 lớp duy
nhất có đủ dữ liệu lịch sử qua OHLCV công khai. Order Flow/Derivatives/
Cross-market/Sentiment không có nguồn lịch sử đủ tốt trong scope hiện tại nên
giữ ở điểm trung tính 50 khi backtest, đúng giới hạn đã ghi từ Phase 1 (xem mục
5b: "Freqtrade/vectorbt backtest offline cho lớp Technical"). Kết quả backtest
vì vậy là cận trên lạc quan hơn thực tế — không dùng số này để quyết định vào
lệnh thật mà không paper trade trước (xem mục 8b, "Lưu ý quan trọng").

**Hệ quả toán học của giới hạn trên:** với 4/6 lớp cố định ở 50 điểm (62% trọng
số), tổng điểm tối đa có thể đạt được trong backtest là `100*0.35 + 90*0.03 +
50*0.62 ≈ 68.7` — LUÔN THẤP HƠN `BUY_SCORE_THRESHOLD` mặc định (70). Nghĩa là
backtest sẽ luôn trả về 0 trade nếu dùng nguyên ngưỡng BUY của live. Dùng tham
số `buy_threshold`/`watch_threshold` của `run_backtest()` để thử ngưỡng thấp
hơn, phù hợp cho mục đích calibrate/so sánh chất lượng lớp Technical+Regime
thuần tuý — không dùng ngưỡng đã hạ này để đổi `BUY_SCORE_THRESHOLD` sống.
"""
import numpy as np
import pandas as pd

from .. import config
from ..indicators import technical
from ..engine import regime as regime_engine
from ..engine import decision, risk

WARMUP_BARS = 210  # đủ cho EMA200 ổn định
NEUTRAL_SCORE = 50.0
# Chi phí lấy từ config để Risk Engine (cost gate) và Backtest Engine luôn dùng
# chung một con số — nếu khai riêng ở 2 nơi, gate sẽ lọc theo chi phí khác với
# chi phí thực sự trừ vào PnL.
DEFAULT_FEE_PCT = config.FEE_PCT
DEFAULT_SLIPPAGE_PCT = config.SLIPPAGE_PCT


def _timeframe_minutes(tf: str) -> int:
    unit = tf[-1]
    value = int(tf[:-1])
    return {"m": value, "h": value * 60, "d": value * 1440}[unit]


def _layer_scores_from_technical(tech_result, regime_result):
    return {
        "technical": tech_result["total"],
        "order_flow": NEUTRAL_SCORE,
        "derivatives": NEUTRAL_SCORE,
        "cross_market": NEUTRAL_SCORE,
        "sentiment": NEUTRAL_SCORE,
        "regime": regime_result["score"],
    }


def run_backtest(
    df: pd.DataFrame,
    symbol: str | None = None,
    timeframe: str | None = None,
    fee_pct: float = DEFAULT_FEE_PCT,
    slippage_pct: float = DEFAULT_SLIPPAGE_PCT,
    buy_threshold: float | None = None,
    watch_threshold: float | None = None,
) -> dict:
    """df: OHLCV thô (cột open/high/low/close/volume), index/thứ tự tăng dần theo thời gian."""
    symbol = symbol or config.SYMBOL
    timeframe = timeframe or config.TIMEFRAME
    n = len(df)
    if n <= WARMUP_BARS + 2:
        raise ValueError(f"Cần tối thiểu {WARMUP_BARS + 2} bar để warmup indicator, chỉ có {n}")

    cooldown_bars = max(1, round(config.COOLDOWN_MINUTES / _timeframe_minutes(timeframe)))
    min_hold_bars = max(1, round(config.MIN_HOLD_MINUTES / _timeframe_minutes(timeframe)))

    # Tính indicator 1 lần cho toàn bộ lịch sử (O(n)) — các indicator trong `ta`
    # đều causal (chỉ nhìn về quá khứ), nên đọc theo `idx` ở mỗi bar là hợp lệ,
    # không phải tính lại trên slice tăng dần mỗi bar (O(n²), quá chậm với lịch
    # sử dài — đã đo thực tế bị treo với 10 ngày dữ liệu 5m trước khi tối ưu).
    enriched = technical.add_indicators(df)

    trades = []
    position = None
    cooldown_until_idx = -1
    n_skipped_cost_gate = 0
    open_vals = df["open"].to_numpy()
    close_vals = df["close"].to_numpy()

    for i in range(WARMUP_BARS, n - 1):  # n-1 vì fill luôn ở bar i+1 (open)
        tech_result = technical.score_from_indicators(enriched, idx=i)
        regime_result = regime_engine.classify_regime(enriched, idx=i)
        layer_scores = _layer_scores_from_technical(tech_result, regime_result)
        total_score = decision.compute_total_score(layer_scores)

        fill_price = float(open_vals[i + 1])

        if position is not None:
            min_hold_satisfied = (i - position["entry_idx"]) >= min_hold_bars
            should_exit, reason = decision.decide_exit(
                position, float(close_vals[i]), enriched, idx=i, min_hold_satisfied=min_hold_satisfied,
            )
            if should_exit:
                exit_price = fill_price * (1 - slippage_pct)
                pnl_pct = risk.compute_pnl_pct(position["entry_price"], exit_price) - fee_pct * 100 * 2
                trades.append(
                    {
                        "entry_idx": position["entry_idx"],
                        "entry_price": position["entry_price"],
                        "exit_idx": i + 1,
                        "exit_price": round(exit_price, 2),
                        "pnl_pct": round(pnl_pct, 3),
                        "reason": reason,
                    }
                )
                cooldown_until_idx = i + 1 + cooldown_bars
                position = None
            continue

        if i < cooldown_until_idx:
            continue

        action, _reason = decision.decide_entry(
            total_score, regime_result["label"], trading_halted=False,
            buy_threshold=buy_threshold, watch_threshold=watch_threshold,
        )
        if action == "BUY":
            entry_price = fill_price * (1 + slippage_pct)
            atr = float(enriched.iloc[i]["atr"])
            if pd.isna(atr) or atr <= 0:
                continue
            plan = risk.compute_position_plan(entry_price, atr, fee_pct=fee_pct, slippage_pct=slippage_pct)
            if not plan["edge_viable"]:
                n_skipped_cost_gate += 1
                continue
            position = {
                "entry_idx": i + 1,
                "entry_price": entry_price,
                "stop_price": plan["stop_price"],
                "take_profit_price": plan["take_profit_price"],
            }

    n_trades = len(trades)
    stats = compute_stats([t["pnl_pct"] for t in trades], config.ACCOUNT_EQUITY_USD)

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "n_bars": n,
        "n_trades": n_trades,
        "trades": trades,
        "fee_pct": fee_pct,
        "slippage_pct": slippage_pct,
        "n_skipped_cost_gate": n_skipped_cost_gate,
        "min_tp_cost_ratio": config.MIN_TP_COST_RATIO,
        **stats,
    }


def compute_stats(trade_pnl_pcts: list[float], init_cash: float) -> dict:
    """Total Return / Max Drawdown / Sharpe (xấp xỉ per-trade) / Win Rate — tự tính,
    không dùng vectorbt (xem docstring module về xung đột pandas với MLflow).
    """
    if not trade_pnl_pcts:
        return {
            "total_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe_ratio": None,
            "win_rate_pct": None,
        }

    equity = [init_cash]
    for pnl_pct in trade_pnl_pcts:
        equity.append(equity[-1] * (1 + pnl_pct / 100))

    equity_arr = np.array(equity)
    peak = np.maximum.accumulate(equity_arr)
    drawdown_pct = (peak - equity_arr) / peak * 100
    max_drawdown_pct = float(drawdown_pct.max())

    total_return_pct = (equity_arr[-1] / equity_arr[0] - 1) * 100

    pnl_arr = np.array(trade_pnl_pcts)
    # Sharpe xấp xỉ theo từng trade (không phải theo kỳ thời gian cố định — số
    # trade không đều nhau về khoảng cách thời gian, đây là giới hạn đã biết).
    sharpe = float(pnl_arr.mean() / pnl_arr.std() * np.sqrt(len(pnl_arr))) if len(pnl_arr) > 1 and pnl_arr.std() > 0 else None
    win_rate_pct = float((pnl_arr > 0).sum() / len(pnl_arr) * 100)

    return {
        "total_return_pct": round(float(total_return_pct), 3),
        "max_drawdown_pct": round(max_drawdown_pct, 3),
        "sharpe_ratio": round(sharpe, 3) if sharpe is not None else None,
        "win_rate_pct": round(win_rate_pct, 2),
    }
