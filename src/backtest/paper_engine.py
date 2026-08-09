"""Backtest "paper test": mô phỏng tick-recompute của Paper Trading ở mức dữ
liệu lịch sử cho phép.

Không mô phỏng lịch activation rời rạc (x phút activation + y phút window) — chạy
liên tục qua toàn bộ lịch sử bar-by-bar như `engine.py`, chỉ khác ở cách chấm
điểm/kiểm tra exit TRONG mỗi bar: dùng OHLCV khung nhỏ hơn (`tick_timeframe`,
mặc định 1m) làm proxy cho tick giá thật, lặp qua từng sub-bar trong bar chính
và tính lại lớp Technical mỗi tick — đúng cách `run.py` tính `live_tech`/
`live_total_score` mỗi poll.

**Giới hạn đã biết:**
- Không có tick thật lịch sử (chỉ có OHLCV) — dùng đường đi OHLC bảo thủ trong
  nến khung nhỏ hơn: Long đi open-low-high-close, Short đi
  open-high-low-close. Khi SL và TP cùng nằm trong một sub-bar, phía bất lợi
  được xét trước; đây là execution assumption, không phải tick thật.
- Vẫn chỉ replay Technical + Regime (như `engine.py`) — Order Flow/Derivatives/
  Cross-market/Sentiment giữ NEUTRAL_SCORE=50, chưa có nguồn lịch sử.
- Chưa mô phỏng MTF confluence (xem `TODO-MTF-CONFLUENCE`).
- Engine trả cả gross và net bằng accounting primitive dùng chung với live.
"""
import numpy as np
import pandas as pd

from .. import config
from ..indicators import technical
from ..engine import regime as regime_engine
from ..engine import decision, risk, support_resistance
from .engine import (
    WARMUP_BARS, NEUTRAL_SCORE, DEFAULT_FEE_PCT, DEFAULT_SLIPPAGE_PCT,
    compute_accounting_stats,
)

INDICATOR_LOOKBACK_BARS = WARMUP_BARS  # đủ ổn định EMA200, không cần recompute toàn lịch sử mỗi tick


def run_staggered_accelerated_replay(*args, **kwargs):
    """Entry point Paper engine cho frozen staggered-pullback historical clock."""
    from .staggered_paper_engine import run_accelerated_replay

    return run_accelerated_replay(*args, **kwargs)


def _layer_scores(technical_total, regime_result):
    return {
        "technical": technical_total,
        "order_flow": NEUTRAL_SCORE,
        "derivatives": NEUTRAL_SCORE,
        "cross_market": NEUTRAL_SCORE,
        "sentiment": NEUTRAL_SCORE,
        "regime": regime_result["score"],
    }


def _tick_score(raw_df, i, tick_price, side="long"):
    """Điểm Technical tại tick giá `tick_price`, coi bar `i` là nến đang hình
    thành (đúng cách `run.py` ghi đè close/high/low nến cuối bằng giá tick).
    Chỉ recompute indicator trên `INDICATOR_LOOKBACK_BARS` bar gần nhất (đủ ổn
    định cho EMA/MACD/ADX/ATR causal) — không phải toàn lịch sử, vì bar < i
    không đổi giá trị khi chỉ sửa bar i (indicator chỉ nhìn về quá khứ)."""
    start = max(0, i - INDICATOR_LOOKBACK_BARS)
    sl = raw_df.iloc[start : i + 1].copy()
    last_idx = sl.index[-1]
    sl.loc[last_idx, "close"] = tick_price
    sl.loc[last_idx, "high"] = max(sl.loc[last_idx, "high"], tick_price)
    sl.loc[last_idx, "low"] = min(sl.loc[last_idx, "low"], tick_price)
    tick_enriched = technical.add_indicators(sl)
    if side == "short":
        tech = technical.score_short_from_indicators(tick_enriched, idx=-1)
    else:
        tech = technical.score_from_indicators(tick_enriched, idx=-1)
    return tech, tick_enriched


def _assign_ticks_to_bars(primary_ts: np.ndarray, tick_ts: np.ndarray) -> np.ndarray:
    """Với mỗi tick, trả về index bar chính mà nó thuộc về (bar `i` chứa các
    tick trong [primary_ts[i], primary_ts[i+1])). Tick trước bar đầu/sau bar
    cuối bị gán -1 (bỏ qua)."""
    idx = np.searchsorted(primary_ts, tick_ts, side="right") - 1
    idx[tick_ts < primary_ts[0]] = -1
    return idx


def run_paper_backtest(
    primary_df: pd.DataFrame,
    tick_df: pd.DataFrame | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    tick_timeframe: str = "1m",
    fee_pct: float = DEFAULT_FEE_PCT,
    slippage_pct: float = DEFAULT_SLIPPAGE_PCT,
    buy_threshold: float | None = None,
    watch_threshold: float | None = None,
    sl_tp_mode: str = "atr",
    swing_window: int = 3,
    swing_lookback: int = 50,
    structural_atr_buffer_mult: float = 0.5,
    funding_rates: list | None = None,
    side: str = "long",
    scoring_profile: str | None = None,
    sr_required_swings: int | None = None,
    collect_timeline: bool = False,
) -> dict:
    """`primary_df`: OHLCV khung chính (khớp `config.TIMEFRAME`). `tick_df`:
    OHLCV khung nhỏ hơn dùng làm proxy tick giá thật trong mỗi bar chính — nếu
    None hoặc thiếu dữ liệu cho 1 bar, bar đó tự fallback về 1 tick duy nhất
    (giá close của bar, giống hành vi `engine.py` chấm theo nến đóng).

    `side`: "long" (mặc định) hoặc "short" (thử nghiệm provisional —
    `src/backtest/short_engine.py` nhưng có tick-recompute, dùng
    `technical.score_short_from_indicators`/`decision.decide_short_entry/exit`/
    `risk.compute_short_position_plan`. `sl_tp_mode="structural"` CHƯA hỗ trợ
    cho short (chỉ có `compute_structural_position_plan` cho long), tự fallback
    về "atr" nếu side="short".

    `sl_tp_mode`: "atr" (mặc định, giống `run.py` live hiện tại) hoặc
    "structural" (SL dưới swing low gần nhất + buffer ATR, TP tại
    swing high gần nhất) — dùng để backtest so sánh 2 hướng trước khi đổi mặc
    định live, xem `technical.find_recent_swing_low/high`.

    `funding_rates`: lịch sử funding rate thật (từ `market.fetch_historical_funding_rates`,
    chỉ có nghĩa khi backtest cho `MARKET_TYPE=swap`, xem `TODO-SWAP-PARITY`) — nếu có,
    trừ thêm chi phí funding thật (Long trả khi funding dương, nhận khi âm)
    vào `net_pnl_pct` theo đúng số lần funding event xảy ra trong lúc giữ lệnh.
    None (mặc định, đúng cho Spot) → không có chi phí funding."""
    symbol = symbol or config.SYMBOL
    timeframe = timeframe or config.TIMEFRAME
    scoring_profile = scoring_profile or config.SCORING_PROFILE
    if scoring_profile not in ("no_trade", "champion", "support_resistance_only"):
        raise ValueError(f"SCORING_PROFILE không hỗ trợ: {scoring_profile}")
    if scoring_profile == "support_resistance_only" and side != "long":
        raise ValueError("support_resistance_only hiện chỉ hỗ trợ Long Spot")
    n = len(primary_df)
    if n <= WARMUP_BARS + 2:
        raise ValueError(f"Cần tối thiểu {WARMUP_BARS + 2} bar để warmup indicator, chỉ có {n}")

    tf_minutes = {"m": 1, "h": 60, "d": 1440}[timeframe[-1]] * int(timeframe[:-1])
    tick_minutes = {"m": 1, "h": 60, "d": 1440}[tick_timeframe[-1]] * int(tick_timeframe[:-1])

    enriched = technical.add_indicators(primary_df)  # dùng để chấm regime (frozen mỗi bar, không theo tick)
    primary_ts = primary_df["ts"].to_numpy()
    open_vals = primary_df["open"].to_numpy()
    close_vals = primary_df["close"].to_numpy()

    if funding_rates:
        funding_ts = np.array([f["timestamp"] for f in funding_rates], dtype="int64")
        funding_rate_vals = np.array([f["fundingRate"] for f in funding_rates], dtype="float64")
    else:
        funding_ts = np.array([], dtype="int64")
        funding_rate_vals = np.array([], dtype="float64")

    def _funding_cost_pct(entry_time, exit_time) -> float:
        if len(funding_ts) == 0:
            return 0.0
        entry_ms = pd.Timestamp(entry_time).value // 1_000_000
        exit_ms = pd.Timestamp(exit_time).value // 1_000_000
        mask = (funding_ts >= entry_ms) & (funding_ts < exit_ms)
        return float(funding_rate_vals[mask].sum()) * 100  # Long trả khi funding dương

    if tick_df is not None and len(tick_df) > 0:
        tick_ts = tick_df["ts"].to_numpy()
        tick_bar_idx = _assign_ticks_to_bars(primary_ts, tick_ts)
    else:
        tick_ts = np.array([])
        tick_bar_idx = np.array([])

    def _ticks_for_bar(i):
        mask = tick_bar_idx == i
        rows = tick_df.loc[mask] if tick_df is not None and len(mask) else None
        if rows is None or rows.empty:
            # fallback: giá close được biết ở cuối bar, không phải đầu bar.
            close_time = pd.Timestamp(primary_df["ts"].iloc[i]) + pd.Timedelta(minutes=tf_minutes)
            return [(close_time, float(close_vals[i]))]
        result = []
        fields = ("open", "high", "low", "close") if side == "short" else ("open", "low", "high", "close")
        step = pd.Timedelta(minutes=tick_minutes) / 4
        for _, row in rows.iterrows():
            ts = pd.Timestamp(row["ts"])
            result.extend((ts + step * j, float(row[field])) for j, field in enumerate(fields))
        return result

    is_short = side == "short"
    if is_short and sl_tp_mode == "structural":
        sl_tp_mode = "atr"  # compute_structural_position_plan chỉ hỗ trợ long, xem docstring

    decide_entry_fn = decision.decide_short_entry if is_short else decision.decide_entry
    decide_exit_fn = decision.decide_short_exit if is_short else decision.decide_exit
    compute_plan_fn = risk.compute_short_position_plan if is_short else risk.compute_position_plan
    entry_action = "SHORT" if is_short else "BUY"
    # Funding: Short nhận khi funding dương (ngược Long trả) -> đảo dấu.
    funding_sign = -1 if is_short else 1

    trades = []
    score_timeline = []
    position = None
    cooldown_until_time = None
    n_skipped_cost_gate = 0
    n_ticks_evaluated = 0
    equity_usd = float(config.ACCOUNT_EQUITY_USD)

    def _record_timeline(tick_time, tick_price, total_score, action, reason=None, sr_result=None):
        if not collect_timeline:
            return
        score_timeline.append({
            "ts": str(tick_time),
            "price": round(float(tick_price), 4),
            "score": round(float(total_score), 4),
            "action": action,
            "reason": reason,
            "score_side": (
                "SELL_BREAKDOWN" if position is not None
                else ("BUY_SUPPORT" if scoring_profile == "support_resistance_only" else side.upper())
            ),
            "support_status": (sr_result or {}).get("support_status"),
            "resistance_status": (sr_result or {}).get("resistance_status"),
            "buy_eligible": (sr_result or {}).get("buy_eligible"),
        })
    effective_threshold = (
        config.SR_DECISION_THRESHOLD if scoring_profile == "support_resistance_only"
        else config.BUY_SCORE_THRESHOLD
    ) if buy_threshold is None else buy_threshold
    if scoring_profile == "no_trade":
        entry_possible = False
        short_circuit_reason = "NO_TRADE_BASELINE"
    elif scoring_profile == "support_resistance_only" and (sr_required_swings or config.SR_REQUIRED_SWINGS) == 1 and effective_threshold > 50:
        entry_possible = False
        short_circuit_reason = "ONE_SWING_MAX_SCORE_50_BELOW_THRESHOLD"
    elif scoring_profile == "champion":
        max_score = sum(
            (100.0 if layer in ("technical", "regime") else NEUTRAL_SCORE) * weight / 100
            for layer, weight in config.WEIGHTS.items()
        )
        entry_possible = effective_threshold <= max_score
        short_circuit_reason = None if entry_possible else f"CHAMPION_MAX_SCORE_{max_score:.2f}_BELOW_THRESHOLD"
    else:
        entry_possible = True
        short_circuit_reason = None

    def _close_position(exit_idx, tick_time, tick_price, reason):
        nonlocal position, equity_usd, cooldown_until_time
        funding_cost_pct = funding_sign * _funding_cost_pct(position["entry_time"], tick_time)
        accounting = risk.compute_trade_accounting(
            position["entry_price"], tick_price, position["size_usd"],
            side=side, fee_pct=fee_pct, slippage_pct=slippage_pct,
            funding_cost_pct=funding_cost_pct, equity_before_usd=equity_usd,
        )
        equity_usd = accounting["equity_after_usd"]
        trades.append({
            "entry_idx": position["entry_idx"],
            "entry_time": str(position["entry_time"]),
            "entry_price": position["entry_price"],
            "exit_idx": exit_idx,
            "exit_time": str(tick_time),
            "exit_price": round(tick_price, 2),
            "gross_pnl_pct": accounting["gross_pnl_pct"],
            "funding_cost_pct": round(funding_cost_pct, 4),
            "net_pnl_pct": accounting["net_pnl_pct"],
            "pnl_usd": accounting["net_pnl_usd"],
            "return_on_equity_pct": accounting["return_on_equity_pct"],
            "size_usd": position["size_usd"],
            "accounting": accounting,
            "scoring_profile": scoring_profile,
            "entry_score": position.get("entry_score"),
            "entry_feature": position.get("entry_feature"),
            "stop_price": position.get("stop_price"),
            "take_profit_price": position.get("take_profit_price"),
            "tp_reason": position.get("tp_reason"),
            "reason": reason,
        })
        cooldown_until_time = pd.Timestamp(tick_time) + pd.Timedelta(minutes=config.COOLDOWN_MINUTES)
        position = None

    for i in range(WARMUP_BARS, n - 1):
        if position is None and not entry_possible:
            continue
        regime_result = regime_engine.classify_regime(enriched, idx=i) if scoring_profile == "champion" else None
        ticks = _ticks_for_bar(i)
        sr_bar = None
        if scoring_profile == "support_resistance_only":
            sr_bar = support_resistance.score(
                enriched, float(close_vals[i]), decision_idx=i - 1,
                required_swings=sr_required_swings,
            )

        def _sr_at(tick_price):
            return support_resistance.score_from_zones(
                tick_price, sr_bar["support_zone"], sr_bar["resistance_zone"],
                sr_bar["atr_current"], decision_idx=i - 1,
                required_swings=sr_bar["required_swings"],
                support_status=sr_bar["support_status"],
                resistance_status=sr_bar["resistance_status"],
                resistance_targets=sr_bar["resistance_targets"],
            )

        if position is not None:
            for tick_time, tick_price in ticks:
                n_ticks_evaluated += 1
                if scoring_profile == "support_resistance_only":
                    sr_result = _sr_at(tick_price)
                    sell_score = support_resistance.breakdown_score(
                        tick_price,
                        (position.get("entry_feature") or {}).get("support_zone"),
                        sr_result["atr_current"],
                    )
                    should_exit, reason = decision.decide_support_resistance_exit(
                        position, tick_price, sell_score, current_time=tick_time,
                    )
                    _record_timeline(
                        tick_time, tick_price, sell_score,
                        "SELL" if should_exit else "HOLD", reason, sr_result,
                    )
                else:
                    _tech, tick_enriched = _tick_score(primary_df, i, tick_price, side=side)
                    should_exit, reason = decide_exit_fn(
                        position, tick_price, tick_enriched, idx=-1, current_time=tick_time,
                    )
                    _record_timeline(
                        tick_time, tick_price, position.get("entry_score", 0),
                        "SELL" if should_exit else "HOLD", reason,
                    )
                if should_exit:
                    _close_position(i, tick_time, tick_price, reason)
                    break
            continue

        for tick_offset, (tick_time, tick_price) in enumerate(ticks):
            if cooldown_until_time is not None and pd.Timestamp(tick_time) < cooldown_until_time:
                continue
            n_ticks_evaluated += 1
            if scoring_profile == "support_resistance_only":
                sr_result = _sr_at(tick_price)
                total_score = sr_result["buy_score"]
                action, _reason = decision.decide_support_resistance_entry(
                    sr_result["buy_score"], sr_result["sell_score"],
                    threshold=buy_threshold,
                    buy_eligible=sr_result["buy_eligible"],
                    ineligible_reason=sr_result["buy_ineligible_reason"],
                )
                tick_enriched = enriched.iloc[:i].copy()
            else:
                sr_result = None
                tech, tick_enriched = _tick_score(primary_df, i, tick_price, side=side)
                layer_scores = _layer_scores(tech["total"], regime_result)
                total_score = decision.compute_total_score(layer_scores)
                pullback_ok = technical.pullback_ok(tick_enriched, -1, side, current_price=tick_price)
                action, _reason = decide_entry_fn(
                    total_score, regime_result["label"], trading_halted=False,
                    buy_threshold=buy_threshold, watch_threshold=watch_threshold,
                    pullback_ok=pullback_ok,
                )
            _record_timeline(tick_time, tick_price, total_score, action, _reason, sr_result)
            if action != entry_action:
                continue
            atr = float(tick_enriched.iloc[-1]["atr"])
            if pd.isna(atr) or atr <= 0:
                continue
            if scoring_profile == "support_resistance_only":
                plan = support_resistance.compute_position_plan(
                    tick_price, atr, sr_result["support_zone"], sr_result["resistance_zone"],
                    resistance_targets=sr_result["resistance_targets"],
                    fee_pct=fee_pct, slippage_pct=slippage_pct,
                    account_equity_usd=equity_usd,
                )
            elif sl_tp_mode == "structural":
                swing_low = technical.find_recent_swing_low(primary_df, idx=i, window=swing_window, lookback=swing_lookback)
                swing_high = technical.find_recent_swing_high(primary_df, idx=i, window=swing_window, lookback=swing_lookback)
                plan = risk.compute_structural_position_plan(
                    tick_price, atr, swing_low, swing_high,
                    atr_buffer_mult=structural_atr_buffer_mult, fee_pct=fee_pct, slippage_pct=slippage_pct,
                    account_equity_usd=equity_usd,
                )
            else:
                plan = compute_plan_fn(
                    tick_price, atr, fee_pct=fee_pct, slippage_pct=slippage_pct,
                    account_equity_usd=equity_usd,
                )
            if not plan["edge_viable"]:
                n_skipped_cost_gate += 1
                continue
            position = {
                "entry_idx": i,
                "entry_time": tick_time,
                "entry_price": tick_price,
                "stop_price": plan["stop_price"],
                "take_profit_price": plan["take_profit_price"],
                "size_usd": plan["size_usd"],
                "tp_reason": plan.get("tp_reason"),
                "entry_score": total_score,
                "entry_feature": sr_result,
            }
            # Entry có thể xảy ra ở open/low của sub-bar 1m. Paper live tiếp tục
            # nhận tick ngay sau đó, nên replay cũng phải xử lý high/close và các
            # sub-bar còn lại thay vì nhảy thẳng sang primary bar kế tiếp.
            for follow_time, follow_price in ticks[tick_offset + 1:]:
                n_ticks_evaluated += 1
                if scoring_profile == "support_resistance_only":
                    follow_sr = _sr_at(follow_price)
                    sell_score = support_resistance.breakdown_score(
                        follow_price,
                        (position.get("entry_feature") or {}).get("support_zone"),
                        follow_sr["atr_current"],
                    )
                    should_exit, exit_reason = decision.decide_support_resistance_exit(
                        position, follow_price, sell_score, current_time=follow_time,
                    )
                    _record_timeline(
                        follow_time, follow_price, sell_score,
                        "SELL" if should_exit else "HOLD", exit_reason, follow_sr,
                    )
                else:
                    _tech, follow_enriched = _tick_score(primary_df, i, follow_price, side=side)
                    should_exit, exit_reason = decide_exit_fn(
                        position, follow_price, follow_enriched, idx=-1,
                        current_time=follow_time,
                    )
                    _record_timeline(
                        follow_time, follow_price, position.get("entry_score", 0),
                        "SELL" if should_exit else "HOLD", exit_reason,
                    )
                if should_exit:
                    _close_position(i, follow_time, follow_price, exit_reason)
                    break
            break
        # Có entry hoặc không, sang bar kế tiếp (profile này tối đa 1 vị thế).

    # Không bỏ mất vị thế đang mở ở ranh giới dataset. Đóng tại close cuối cùng
    # với reason riêng để PnL/trade count reconcile; đây là mark-to-market của
    # phép đo, không phải tín hiệu chiến lược.
    if position is not None:
        exit_time = pd.Timestamp(primary_df["ts"].iloc[-1]) + pd.Timedelta(minutes=tf_minutes)
        exit_price = float(close_vals[-1])
        funding_cost_pct = funding_sign * _funding_cost_pct(position["entry_time"], exit_time)
        accounting = risk.compute_trade_accounting(
            position["entry_price"], exit_price, position["size_usd"],
            side=side, fee_pct=fee_pct, slippage_pct=slippage_pct,
            funding_cost_pct=funding_cost_pct, equity_before_usd=equity_usd,
        )
        equity_usd = accounting["equity_after_usd"]
        trades.append({
            "entry_idx": position["entry_idx"],
            "entry_time": str(position["entry_time"]),
            "entry_price": position["entry_price"],
            "exit_idx": n - 1,
            "exit_time": str(exit_time),
            "exit_price": round(exit_price, 2),
            "gross_pnl_pct": accounting["gross_pnl_pct"],
            "funding_cost_pct": round(funding_cost_pct, 4),
            "net_pnl_pct": accounting["net_pnl_pct"],
            "pnl_usd": accounting["net_pnl_usd"],
            "return_on_equity_pct": accounting["return_on_equity_pct"],
            "size_usd": position["size_usd"],
            "accounting": accounting,
            "scoring_profile": scoring_profile,
            "entry_score": position.get("entry_score"),
            "entry_feature": position.get("entry_feature"),
            "stop_price": position.get("stop_price"),
            "take_profit_price": position.get("take_profit_price"),
            "tp_reason": position.get("tp_reason"),
            "reason": "END_OF_DATA_MARK_TO_MARKET",
        })

    n_trades = len(trades)
    gross_stats = compute_accounting_stats(trades, config.ACCOUNT_EQUITY_USD, pnl_key="gross_pnl_usd")
    net_stats = compute_accounting_stats(trades, config.ACCOUNT_EQUITY_USD, pnl_key="net_pnl_usd")

    return {
        "symbol": symbol,
        "side": side,
        "timeframe": timeframe,
        "tick_timeframe": tick_timeframe,
        "sl_tp_mode": sl_tp_mode,
        "scoring_profile": scoring_profile,
        "sr_required_swings": sr_required_swings or config.SR_REQUIRED_SWINGS,
        "n_bars": n,
        "n_ticks_evaluated": n_ticks_evaluated,
        "n_trades": n_trades,
        "trades": trades,
        "fee_pct": fee_pct,
        "slippage_pct": slippage_pct,
        "n_skipped_cost_gate": n_skipped_cost_gate,
        "execution_assumption": "subbar_ohlc_adverse_first",
        "end_of_data_policy": "close_at_last_closed_bar",
        "max_concurrent_positions": 1,
        "short_circuit_reason": short_circuit_reason,
        "score_timeline": score_timeline if collect_timeline else None,
        "min_tp_cost_ratio": config.MIN_TP_COST_RATIO,
        "gross": gross_stats,
        "net": net_stats,       # đã trừ round-trip cost — kinh tế học thật
        **net_stats,            # backward-compat với format engine.py cũ (total_return_pct/... = bản net)
    }
