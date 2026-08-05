"""Entrypoint gọi bởi cron/launchd — mỗi lần chạy là 1 process độc lập.

Pipeline (xem plan-02.md, phần Tool/Skill breakdown):
fetch_market -> indicators -> orderflow -> derivatives -> regime -> sentiment
-> decision -> report (LLM, chỉ khi BUY/SELL) -> telegram.notify

Mục 5d: cron kích hoạt mỗi x phút (launchd/cron, ngoài code này), nhưng process
không thoát ngay — mở cửa sổ theo dõi liên tục `MONITOR_WINDOW_MINUTES` phút,
trong đó đọc tick giá thật từ `collector_ws.py` (qua `state_store.get_last_tick`)
để đánh giá decide_entry/decide_exit nhiều lần thay vì 1 snapshot duy nhất.
Indicator/score (OHLCV, order book, funding, OI...) vẫn chỉ tính 1 lần từ REST
mỗi khi cron kích hoạt — chỉ riêng giá dùng để so khớp stop/take-profit/ngưỡng
là được refresh theo tick trong cửa sổ.
"""
import sys
import time
from datetime import datetime, timezone

from . import config, state_store
from .data import market, crossmarket, sentiment as sentiment_data
from .indicators import technical
from .engine import orderflow, derivatives, regime as regime_engine
from .engine import sentiment_score, crossmarket_score, decision, risk
from .notify import telegram, ai_report

PRIMARY_TF = "5m"
TIMEFRAMES = ("1m", "5m", "15m")


def _notify(text):
    sent = telegram.send_message(text)
    if not sent:
        print(f"[telegram fallback] {text}")


def main():
    try:
        with state_store.run_lock():
            _run_once()
        state_store.record_run_health(ok=True)
    except state_store.RunAlreadyInProgress as e:
        print(f"Bỏ qua lần chạy: {e}")
    except Exception as e:
        state_store.record_run_health(ok=False)
        print(f"Lỗi trong lần chạy: {e}", file=sys.stderr)
        raise


def _current_price(fallback_price):
    """Tick giá thật từ collector_ws nếu có (mục 5d); fallback về giá REST đầu
    cửa sổ nếu collector_ws chưa chạy/mất kết nối — không chặn pipeline."""
    tick = state_store.get_last_tick(config.SYMBOL)
    return tick if tick is not None else fallback_price


def _min_hold_satisfied(entry_time_iso: str) -> bool:
    entry_time = datetime.fromisoformat(entry_time_iso)
    elapsed_minutes = (datetime.now(timezone.utc) - entry_time).total_seconds() / 60
    return elapsed_minutes >= config.MIN_HOLD_MINUTES


def _handle_exit(position, price, total_score, layer_scores, primary_with_indicators):
    should_exit, exit_reason = decision.decide_exit(
        position, price, primary_with_indicators,
        min_hold_satisfied=_min_hold_satisfied(position["entry_time"]),
    )
    if not should_exit:
        return False
    pnl_pct = risk.compute_pnl_pct(position["entry_price"], price)
    trade_id = state_store.make_trade_id(position["symbol"], position["entry_time"])
    state_store.add_daily_pnl(pnl_pct)
    state_store.set_position_state("WAIT")
    state_store.record_exit_now()
    state_store.log_signal(config.SYMBOL, price, "SELL", total_score, layer_scores, exit_reason)
    state_store.log_event(
        "EXIT",
        {"market": {"price": price}, "reason": exit_reason, "pnl_pct": pnl_pct},
        trade_id=trade_id,
    )
    report = ai_report.generate_report(
        config.SYMBOL, "SELL", price, total_score, layer_scores,
        f"{exit_reason} — P&L {pnl_pct}%",
    )
    _notify(report)
    return True


def _handle_entry(price, total_score, regime_label, trading_halted, primary_with_indicators, raw_features, layer_scores):
    kill_switch_on = state_store.is_kill_switch_on()
    cooldown_remaining = state_store.cooldown_remaining_seconds(config.COOLDOWN_MINUTES)
    action, reason = decision.decide_entry(
        total_score,
        regime_label,
        trading_halted,
        kill_switch_on=kill_switch_on,
        kill_switch_reason=state_store.get_kill_switch_reason(),
        cooldown_remaining_seconds=cooldown_remaining,
    )

    if action == "IGNORE" and (kill_switch_on or cooldown_remaining > 0 or trading_halted):
        state_store.log_event("RISK_REJECTED", {"reason": reason, "total_score": total_score})
    elif action != "IGNORE":
        state_store.log_event("SIGNAL_GENERATED", {"decision": action, "reason": reason, "total_score": total_score})

    if action != "BUY":
        return action, reason, False

    atr = float(primary_with_indicators.iloc[-1]["atr"])
    plan = risk.compute_position_plan(price, atr)
    # Cost gate (xem docs/research-technical-signal-edge.md mục 6.1): biến động quá
    # nhỏ so với chi phí giao dịch thì lệnh lỗ ngay cả khi chạm đúng Take Profit —
    # huỷ tín hiệu ở đây thay vì để Risk Engine cấp size cho một lệnh không thể lãi.
    if not plan["edge_viable"]:
        state_store.log_event("RISK_REJECTED", {
            "reason": plan["skip_reason"], "total_score": total_score,
            "tp_distance_pct": plan["tp_distance_pct"],
            "round_trip_cost_pct": plan["round_trip_cost_pct"],
        })
        return "IGNORE", plan["skip_reason"], False

    entry_time = datetime.now(timezone.utc).isoformat()
    state_store.set_position_state(
        "IN_POSITION",
        symbol=config.SYMBOL,
        entry_price=price,
        entry_time=entry_time,
        stop_price=plan["stop_price"],
        take_profit_price=plan["take_profit_price"],
        size_usd=plan["size_usd"],
    )
    trade_id = state_store.make_trade_id(config.SYMBOL, entry_time)
    state_store.log_event(
        "ENTRY",
        {
            "market": {"price": price},
            "feature": raw_features,
            "risk": {
                "position_usd": plan["size_usd"],
                "sl": plan["stop_price"],
                "tp": plan["take_profit_price"],
            },
            "model": "rule_engine_v1",
        },
        trade_id=trade_id,
    )
    report = ai_report.generate_report(
        config.SYMBOL, "BUY", price, total_score, layer_scores,
        f"{reason}. Stop {plan['stop_price']}, Target {plan['take_profit_price']}, "
        f"Size ${plan['size_usd']}",
    )
    _notify(report)
    return action, reason, True


def _run_once():
    exchange = market.get_exchange()

    try:
        raw_ohlcv = market.fetch_ohlcv_multi_tf(exchange, config.SYMBOL, TIMEFRAMES, limit=250)
        order_book = market.fetch_order_book(exchange, config.SYMBOL)
        trades = market.fetch_recent_trades(exchange, config.SYMBOL)
    except Exception as e:
        print(f"Fetch dữ liệu lỗi, bỏ qua lần chạy này: {e}", file=sys.stderr)
        return

    funding_rate = market.fetch_funding_rate(exchange, config.SYMBOL)
    open_interest = market.fetch_open_interest(exchange, config.SYMBOL)
    cross_market_changes = crossmarket.fetch_cross_market_changes()
    fear_greed = sentiment_data.fetch_fear_greed()

    df_by_tf = {tf: technical.to_dataframe(o) for tf, o in raw_ohlcv.items()}
    tech_result = technical.compute_technical_score(df_by_tf, primary_tf=PRIMARY_TF)
    snapshot_price = tech_result["last_price"]

    primary_with_indicators = technical.add_indicators(df_by_tf[PRIMARY_TF])
    regime_result = regime_engine.classify_regime(primary_with_indicators)

    # CVD ưu tiên lấy từ collector_ws.py (WS thật) thay REST snapshot khi có sẵn (mục 7b)
    ws_cvd = state_store.get_ws_cvd(config.SYMBOL)
    order_flow_result = orderflow.compute_order_flow_score(order_book, trades, ws_cvd=ws_cvd)
    derivatives_result = derivatives.compute_derivatives_score(funding_rate, open_interest, snapshot_price)
    sentiment_result = sentiment_score.compute_sentiment_score(fear_greed)
    cross_market_result = crossmarket_score.compute_cross_market_score(cross_market_changes)

    layer_scores = {
        "technical": tech_result["total"],
        "order_flow": order_flow_result["total"],
        "derivatives": derivatives_result["total"],
        "cross_market": cross_market_result["total"],
        "sentiment": sentiment_result["total"],
        "regime": regime_result["score"],
    }
    total_score = decision.compute_total_score(layer_scores)

    # Collector sàn thứ 2 (Binance, mục 5b) — chỉ để đối chiếu giá vào Feature Store,
    # KHÔNG tham gia layer_scores/Rule Engine. Lỗi/None không chặn pipeline chính.
    binance_price = market.fetch_cross_exchange_price(config.SYMBOL)
    binance_price_diff_pct = (
        round((binance_price - snapshot_price) / snapshot_price * 100, 4) if binance_price else None
    )

    # Feature Store + Raw Event (mục 5b/13.1-13.3): raw feature tách khỏi score, lưu mỗi
    # lần chạy để train Entry Model (Phase 3) và làm nền Feature Lineage (mục 13.11).
    raw_features = {
        "technical": tech_result["raw"],
        "order_flow": order_flow_result["raw"],
        "derivatives": derivatives_result["raw"],
        "cross_market": cross_market_result["raw"],
        "sentiment": sentiment_result["raw"],
        "regime": regime_result["raw"],
        "binance_cross_check": {"price": binance_price, "diff_pct_vs_primary": binance_price_diff_pct},
    }
    state_store.log_feature_snapshot(config.SYMBOL, snapshot_price, raw_features)
    state_store.log_event("FEATURE_UPDATED", {"total_score": total_score, "layer_scores": layer_scores})

    trading_halted = state_store.is_trading_halted_today()

    # Max Drawdown (mục 8 Risk Engine): vượt ngưỡng thì tự bật Kill Switch, không tự tắt lại
    # — cần người kiểm tra và tắt thủ công (xem scripts/kill_switch.py) trước khi trade tiếp.
    max_dd = state_store.get_max_drawdown_pct()
    if max_dd >= config.MAX_DRAWDOWN_PCT and not state_store.is_kill_switch_on():
        state_store.set_kill_switch(True, reason=f"Max drawdown {max_dd}% >= ngưỡng {config.MAX_DRAWDOWN_PCT}%")

    # Cửa sổ theo dõi liên tục (mục 5d): indicator/score ở trên tính 1 lần, nhưng
    # decide_entry/decide_exit được đánh giá lại mỗi MONITOR_POLL_SECONDS bằng tick
    # giá thật, trong suốt MONITOR_WINDOW_MINUTES — thay vì chỉ 1 lần rồi thoát.
    window_deadline = time.monotonic() + config.MONITOR_WINDOW_MINUTES * 60
    last_logged_action = None

    while True:
        price = _current_price(snapshot_price)
        position = state_store.get_position_state()

        if position["status"] == "IN_POSITION":
            exited = _handle_exit(position, price, total_score, layer_scores, primary_with_indicators)
            if exited:
                last_logged_action = "SELL"
            elif last_logged_action != "HOLD":
                state_store.log_signal(config.SYMBOL, price, "HOLD", total_score, layer_scores, "Đang theo dõi vị thế mở")
                last_logged_action = "HOLD"
        else:
            action, reason, entered = _handle_entry(
                price, total_score, regime_result["label"], trading_halted,
                primary_with_indicators, raw_features, layer_scores,
            )
            if action != last_logged_action:
                state_store.log_signal(config.SYMBOL, price, action, total_score, layer_scores, reason)
                last_logged_action = action

        if time.monotonic() >= window_deadline:
            break
        time.sleep(config.MONITOR_POLL_SECONDS)


if __name__ == "__main__":
    main()
