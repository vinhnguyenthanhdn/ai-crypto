"""Backtest Engine cho chiến lược Short riêng — thử nghiệm (phát hiện "buy đỉnh
cục bộ" từ AI Review Backtest trên chiến lược Long).

Mirror hoàn toàn cấu trúc của `engine.py` (cùng nguyên tắc không look-ahead,
fill ở open bar kế tiếp, warmup, min-hold-time...) nhưng dùng
`technical.score_short_from_indicators` + `decision.decide_short_entry/exit` +
`risk.compute_short_position_plan/compute_short_pnl_pct`.

**CHƯA dùng cho Rule Engine live/`run.py`** — chỉ để trả lời câu hỏi nghiên cứu
"Short trên đúng bộ tín hiệu bearish mirror có edge thật không", tách biệt khỏi
kết luận tautological "đảo dấu lệnh Long đã thua" (xem giải thích trong hội
thoại — đó chỉ là phép tính đối xứng trên cùng dữ liệu, không phải bằng chứng
độc lập). Chiến lược Short ở đây có bộ điều kiện entry/exit RIÊNG, độc lập.
"""
import pandas as pd

from .. import config
from ..indicators import technical
from ..engine import regime as regime_engine
from ..engine import decision, risk
from .engine import WARMUP_BARS, NEUTRAL_SCORE, DEFAULT_FEE_PCT, DEFAULT_SLIPPAGE_PCT, _timeframe_minutes, compute_stats


def _layer_scores_from_technical(tech_result, regime_result):
    return {
        "technical": tech_result["total"],
        "order_flow": NEUTRAL_SCORE,
        "derivatives": NEUTRAL_SCORE,
        "cross_market": NEUTRAL_SCORE,
        "sentiment": NEUTRAL_SCORE,
        "regime": regime_result["score"],
    }


def run_backtest_short(
    df: pd.DataFrame,
    symbol: str | None = None,
    timeframe: str | None = None,
    fee_pct: float = DEFAULT_FEE_PCT,
    slippage_pct: float = DEFAULT_SLIPPAGE_PCT,
    short_threshold: float | None = None,
    watch_threshold: float | None = None,
) -> dict:
    symbol = symbol or config.SYMBOL
    timeframe = timeframe or config.TIMEFRAME
    n = len(df)
    if n <= WARMUP_BARS + 2:
        raise ValueError(f"Cần tối thiểu {WARMUP_BARS + 2} bar để warmup indicator, chỉ có {n}")

    cooldown_bars = max(1, round(config.COOLDOWN_MINUTES / _timeframe_minutes(timeframe)))
    min_hold_bars = max(1, round(config.MIN_HOLD_MINUTES / _timeframe_minutes(timeframe)))

    enriched = technical.add_indicators(df)

    trades = []
    position = None
    cooldown_until_idx = -1
    n_skipped_cost_gate = 0
    open_vals = df["open"].to_numpy()
    close_vals = df["close"].to_numpy()

    for i in range(WARMUP_BARS, n - 1):
        tech_result = technical.score_short_from_indicators(enriched, idx=i)
        regime_result = regime_engine.classify_regime(enriched, idx=i)
        layer_scores = _layer_scores_from_technical(tech_result, regime_result)
        total_score = decision.compute_total_score(layer_scores)

        fill_price = float(open_vals[i + 1])

        if position is not None:
            min_hold_satisfied = (i - position["entry_idx"]) >= min_hold_bars
            should_exit, reason = decision.decide_short_exit(
                position, float(close_vals[i]), enriched, idx=i, min_hold_satisfied=min_hold_satisfied,
            )
            if should_exit:
                # Short: mua lại (cover) ở open bar kế tiếp — slippage bất lợi là giá CAO hơn khi cover
                exit_price = fill_price * (1 + slippage_pct)
                pnl_pct = risk.compute_short_pnl_pct(position["entry_price"], exit_price) - fee_pct * 100 * 2
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

        action, _reason = decision.decide_short_entry(
            total_score, regime_result["label"], trading_halted=False,
            buy_threshold=short_threshold, watch_threshold=watch_threshold,
        )
        if action == "SHORT":
            # Short: bán ở open bar kế tiếp — slippage bất lợi là giá THẤP hơn khi bán
            entry_price = fill_price * (1 - slippage_pct)
            atr = float(enriched.iloc[i]["atr"])
            if pd.isna(atr) or atr <= 0:
                continue
            plan = risk.compute_short_position_plan(entry_price, atr, fee_pct=fee_pct, slippage_pct=slippage_pct)
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
        "side": "short",
        "n_bars": n,
        "n_trades": n_trades,
        "trades": trades,
        "fee_pct": fee_pct,
        "slippage_pct": slippage_pct,
        "n_skipped_cost_gate": n_skipped_cost_gate,
        "min_tp_cost_ratio": config.MIN_TP_COST_RATIO,
        **stats,
    }
