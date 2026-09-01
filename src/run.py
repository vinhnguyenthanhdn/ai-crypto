"""Entrypoint Rule Engine; có thể chạy một cycle hoặc daemon liên tục.

Pipeline:
fetch_market -> indicators -> orderflow -> derivatives -> regime -> sentiment
-> decision -> report (LLM, chỉ khi BUY/SELL) -> telegram.notify

Paper launchd giữ một scheduler nhẹ bằng `KeepAlive`; `RUN_SCHEDULED=true` neo
activation theo start-to-start interval. Mỗi activation mở cửa sổ
`MONITOR_WINDOW_MINUTES`, trong đó
đọc tick giá thật từ `collector_ws.py` (qua `state_store.get_last_tick`) để đánh
giá decide_entry/decide_exit nhiều lần thay vì 1 snapshot duy nhất.
Indicator/score (OHLCV, order book, funding, OI...) được refresh đầu mỗi cycle;
giá dùng cho stop/take-profit/ngưỡng được refresh mỗi poll từ WS hoặc REST tươi.
"""
import sys
import time
import traceback
from datetime import datetime, timezone

from . import config, state_store
from .data import market, crossmarket, sentiment as sentiment_data
from .indicators import technical
from .engine import orderflow, derivatives, regime as regime_engine, support_resistance
from .engine import sentiment_score, crossmarket_score, decision, risk
from .notify import telegram, ai_report

PRIMARY_TF = config.TIMEFRAME
TIMEFRAMES = config.MTF_TIMEFRAMES
# config.SYMBOL giữ dạng "sạch" (BTC/USDT) cho display/log/Telegram/position_state
# — EXCHANGE_SYMBOL là symbol CCXT unified thật để gọi exchange (khác nhau khi
# MARKET_TYPE=swap, xem market.resolve_symbol + docs/decisions.md).
EXCHANGE_SYMBOL = market.resolve_symbol(config.SYMBOL)
# Derivatives là context từ perpetual contract kể cả khi execution là Spot.
DERIVATIVES_SYMBOL = market.resolve_symbol(config.SYMBOL, "swap")
LEGACY_SCORE_LAYERS = ("technical", "order_flow", "derivatives", "cross_market", "sentiment", "regime")


def _sr_scoring_view(score: float) -> tuple[dict, dict]:
    """Presentation/decision weights cho S/R-only; layer khác bằng 0 tuyệt đối."""
    layers = {"support_resistance": round(float(score), 2)}
    layers.update({layer: 0.0 for layer in LEGACY_SCORE_LAYERS})
    weights = {"support_resistance": 100}
    weights.update({layer: 0 for layer in LEGACY_SCORE_LAYERS})
    return layers, weights


def _zone_monitor(zone: dict | None, price: float, atr: float) -> dict:
    if not zone:
        return {"available": False}
    low, high = float(zone["low"]), float(zone["high"])
    if price < low:
        relation, distance = "BELOW", low - price
    elif price > high:
        relation, distance = "ABOVE", price - high
    else:
        relation, distance = "INSIDE", 0.0
    swings = zone.get("swings") or []
    return {
        "available": True,
        "kind": zone.get("kind"),
        "low": low,
        "high": high,
        "width_usd": round(high - low, 8),
        "relation": relation,
        "distance_usd": round(distance, 8),
        "distance_atr": round(distance / atr, 6) if atr > 0 else None,
        "touch_count": zone.get("touch_count", len(swings)),
        "atr_formation": zone.get("atr_form"),
        "swing_prices": [s.get("price") for s in swings],
        "swing_times": [s.get("ts") for s in swings],
    }


def _sr_monitor_snapshot(price: float, sr_result: dict, score_side: str,
                         effective_score: float, open_positions: list) -> dict:
    atr = float(sr_result.get("atr_current") or 0.0)
    entry_supports = []
    for position in open_positions:
        if position.get("scoring_profile") != "support_resistance_only":
            continue
        entry_sr = (position.get("position_meta") or {}).get("support_resistance") or {}
        zone = entry_sr.get("support_zone")
        if zone:
            entry_supports.append({
                "trade_id": position.get("trade_id"),
                "zone": _zone_monitor(zone, price, atr),
                "breakdown_score": support_resistance.breakdown_score(price, zone, atr),
            })
    return {
        "current_price": price,
        "atr_current": atr,
        "decision_idx": sr_result.get("decision_idx"),
        "threshold": sr_result.get("threshold"),
        "score_side": score_side,
        "effective_score": effective_score,
        "buy_score": sr_result.get("buy_score", 0.0),
        "sell_score_current_zone": sr_result.get("sell_score", 0.0),
        "support_status": sr_result.get("support_status", "NO_SUPPORT"),
        "resistance_status": sr_result.get("resistance_status", "NO_RESISTANCE"),
        "buy_eligible": bool(sr_result.get("buy_eligible", False)),
        "buy_ineligible_reason": sr_result.get("buy_ineligible_reason"),
        "required_swings": sr_result.get("required_swings"),
        "resistance_target_prices": [
            target.get("low") for target in sr_result.get("resistance_targets", [])
        ],
        "support": _zone_monitor(sr_result.get("support_zone"), price, atr),
        "resistance": _zone_monitor(sr_result.get("resistance_zone"), price, atr),
        "entry_supports": entry_supports,
    }


def _notify(text):
    if config.STRATEGY_LABEL:
        text = f"[{config.STRATEGY_LABEL}] {text}"
    sent = telegram.send_message(text)
    if not sent:
        print(f"[telegram fallback] {text}")


def main():
    if config.BTC_SPOT_TREND_ENABLED:
        raise RuntimeError(
            "BTC_SPOT_TREND_ENABLED chưa được phép bật: historical parity đã pass "
            "nhưng daily venue order-resize forward Paper chưa được nối"
        )
    if config.STAGGERED_PULLBACK_ENABLED:
        raise RuntimeError(
            "STAGGERED_PULLBACK_ENABLED chưa được phép bật: offline trade parity đã pass "
            "nhưng live Spot/Swap two-sided lifecycle parity chưa hoàn tất"
        )
    if config.RUN_SCHEDULED:
        _run_scheduled()
        return
    try:
        with state_store.run_lock(stale_after_seconds=config.RUN_LOCK_STALE_MINUTES * 60) as lock_owner:
            while True:
                ok = _run_once(lock_owner=lock_owner)
                state_store.record_run_health(ok=ok)
                if not config.RUN_CONTINUOUS:
                    break
                if not ok:
                    time.sleep(config.CONTINUOUS_RETRY_SECONDS)
    except state_store.RunAlreadyInProgress as e:
        print(f"Bỏ qua lần chạy: {e}")
    except Exception as e:
        state_store.record_run_health(ok=False)
        print(f"Lỗi trong lần chạy: {e}", file=sys.stderr)
        raise


def _advance_scheduled_start(previous_start: float, now: float,
                             interval_seconds: float) -> float:
    """Mốc start-to-start kế tiếp; bỏ qua slot đã lỡ, không chạy catch-up dồn."""
    next_start = previous_start + interval_seconds
    while next_start <= now:
        next_start += interval_seconds
    return next_start


def _run_scheduled():
    """Scheduler nhẹ luôn sống; chỉ giữ run lock trong monitoring window.

    Nhịp được neo theo thời điểm BẮT ĐẦU activation, nên 60 phút / window 50
    phút tạo khoảng nghỉ xấp xỉ 10 phút thay vì thành 110 phút.
    """
    interval_seconds = max(60.0, config.ACTIVATION_INTERVAL_MINUTES * 60)
    next_start = time.monotonic()
    while True:
        wait_seconds = next_start - time.monotonic()
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        activation_start = next_start
        try:
            with state_store.run_lock(
                stale_after_seconds=config.RUN_LOCK_STALE_MINUTES * 60,
            ) as lock_owner:
                while True:
                    ok = _run_once(lock_owner=lock_owner)
                    state_store.record_run_health(ok=ok)
                    if ok:
                        break
                    retry_seconds = max(1.0, config.CONTINUOUS_RETRY_SECONDS)
                    # Một lỗi fetch đầu window không được làm mất cả activation;
                    # chỉ retry nếu vẫn còn thời gian trước slot start kế tiếp.
                    if time.monotonic() + retry_seconds >= activation_start + interval_seconds:
                        break
                    time.sleep(retry_seconds)
        except state_store.RunAlreadyInProgress as exc:
            print(f"Bỏ qua activation trùng: {exc}", flush=True)
        except Exception as exc:  # scheduler phải sống để thử lại ở slot sau
            state_store.record_run_health(ok=False)
            print(f"Lỗi trong activation: {exc}", file=sys.stderr, flush=True)
            traceback.print_exc()
        next_start = _advance_scheduled_start(
            activation_start, time.monotonic(), interval_seconds,
        )


def _current_price(exchange):
    """Chỉ trả giá có timestamp đủ mới; WS stale thì fetch REST ngay tại poll."""
    tick = state_store.get_last_tick_snapshot(
        config.SYMBOL, max_age_seconds=config.MARKET_TICK_MAX_AGE_SECONDS,
    )
    if tick is not None:
        return tick
    snapshot = market.fetch_ticker_snapshot(exchange, EXCHANGE_SYMBOL)
    age = max(0.0, time.time() - snapshot["timestamp_ms"] / 1000)
    if age > config.MARKET_TICK_MAX_AGE_SECONDS:
        raise RuntimeError(
            f"REST ticker stale {age:.1f}s > {config.MARKET_TICK_MAX_AGE_SECONDS}s"
        )
    return {
        "price": snapshot["price"],
        "timestamp": datetime.fromtimestamp(
            snapshot["timestamp_ms"] / 1000, tz=timezone.utc,
        ).isoformat(),
        "age_seconds": age,
        "source": snapshot["source"],
    }


def _handle_exit(position, price, total_score, layer_scores, primary_with_indicators,
                 sr_result=None):
    if position.get("scoring_profile") == "support_resistance_only":
        entry_sr = (position.get("position_meta") or {}).get("support_resistance") or {}
        sell_score = support_resistance.breakdown_score(
            price, entry_sr.get("support_zone"), (sr_result or {}).get("atr_current", 0.0),
        )
        should_exit, exit_reason = decision.decide_support_resistance_exit(
            position, price, sell_score,
            current_time=datetime.now(timezone.utc),
        )
    else:
        should_exit, exit_reason = decision.decide_exit(
            position, price, primary_with_indicators, current_time=datetime.now(timezone.utc),
        )
    if not should_exit:
        return False, exit_reason
    trade_id = position["trade_id"]
    equity_before = state_store.get_current_equity_usd()
    accounting = risk.compute_trade_accounting(
        position["entry_price"], price, position["size_usd"], side="long",
        equity_before_usd=equity_before,
    )
    ledger = state_store.record_trade_accounting(trade_id, accounting)
    accounting = ledger["accounting"]
    state_store.close_position(trade_id)
    state_store.record_exit_now()
    state_store.log_signal(config.SYMBOL, price, "SELL", total_score, layer_scores, exit_reason)
    state_store.log_event(
        "EXIT",
        {
            "market": {"price": price}, "reason": exit_reason,
            "pnl_pct": accounting["return_on_equity_pct"],
            "net_pnl_pct": accounting["net_pnl_pct"],
            "gross_pnl_pct": accounting["gross_pnl_pct"],
            "pnl_usd": accounting["net_pnl_usd"],
            "accounting": accounting,
        },
        trade_id=trade_id,
    )
    report = ai_report.generate_report_cached(
        f"SELL:{trade_id}", config.SYMBOL, "SELL", price, total_score, layer_scores,
        f"{exit_reason} — P&L ${accounting['net_pnl_usd']:.2f} "
        f"({accounting['return_on_equity_pct']:.3f}% equity, net)",
    )
    _notify(report)
    return True, exit_reason


def _handle_entry(price, total_score, regime_label, trading_halted, primary_with_indicators, raw_features,
                   layer_scores, binance_price_diff_pct=None, sr_result=None):
    kill_switch_on = state_store.is_kill_switch_on()
    cooldown_remaining = state_store.cooldown_remaining_seconds(config.COOLDOWN_MINUTES)
    is_sr = config.SCORING_PROFILE == "support_resistance_only"
    pullback_ok = True if is_sr else technical.pullback_ok(
        primary_with_indicators, -1, "long", current_price=price,
    )
    if kill_switch_on:
        action, reason = "IGNORE", f"Kill switch đang bật ({state_store.get_kill_switch_reason() or 'không rõ lý do'})"
    elif cooldown_remaining > 0:
        action, reason = "IGNORE", f"Đang trong cooldown, còn {round(cooldown_remaining)}s"
    elif trading_halted:
        action, reason = "IGNORE", "Đã chạm daily loss limit"
    elif is_sr:
        action, reason = decision.decide_support_resistance_entry(
            (sr_result or {}).get("buy_score", 0.0),
            (sr_result or {}).get("sell_score", 0.0),
            buy_eligible=(sr_result or {}).get("buy_eligible", False),
            ineligible_reason=(sr_result or {}).get("buy_ineligible_reason"),
        )
    else:
        action, reason = decision.decide_entry(
            total_score, regime_label, trading_halted,
            kill_switch_on=False, cooldown_remaining_seconds=0, pullback_ok=pullback_ok,
        )

    if action == "IGNORE" and (kill_switch_on or cooldown_remaining > 0 or trading_halted):
        gate = "kill_switch" if kill_switch_on else ("cooldown" if cooldown_remaining > 0 else "daily_loss_halt")
        state_store.log_event("RISK_REJECTED", {"reason": reason, "total_score": total_score, "gate": gate})
    elif action == "BUY":
        state_store.log_event("BUY_CANDIDATE", {"decision": action, "reason": reason, "total_score": total_score})
    elif action != "IGNORE":
        state_store.log_event("SIGNAL_GENERATED", {"decision": action, "reason": reason, "total_score": total_score})

    if action != "BUY":
        return action, reason, False, pullback_ok

    # Basis-risk gate (`TODO-BASIS-GATE`) — mặc định TẮT.
    # Không dùng giá Binance để tính score/entry — chỉ veto nếu lệch bất thường
    # so với sàn thực thi (OKX), dấu hiệu lỗi data hơn là tín hiệu giao dịch.
    if (config.CROSS_EXCHANGE_DIVERGENCE_GATE_ENABLED and binance_price_diff_pct is not None
            and abs(binance_price_diff_pct) > config.MAX_CROSS_EXCHANGE_DIVERGENCE_PCT):
        reason = (
            f"Giá Binance lệch {binance_price_diff_pct}% so với OKX, vượt ngưỡng "
            f"{config.MAX_CROSS_EXCHANGE_DIVERGENCE_PCT}% — nghi ngờ lỗi data, không vào lệnh"
        )
        state_store.log_event("RISK_REJECTED", {"reason": reason, "total_score": total_score, "gate": "basis_risk"})
        return "IGNORE", reason, False, pullback_ok

    open_positions = state_store.get_open_positions()
    max_positions = 1 if is_sr else config.MAX_CONCURRENT_POSITIONS
    if len(open_positions) >= max_positions:
        reason = f"Đã đạt số lệnh mở tối đa ({max_positions})"
        state_store.log_event("RISK_REJECTED", {"reason": reason, "total_score": total_score, "gate": "max_concurrent_positions"})
        return "IGNORE", reason, False, pullback_ok

    atr = (
        float((sr_result or {}).get("atr_current") or 0.0)
        if is_sr else float(primary_with_indicators.iloc[-1]["atr"])
    )
    already_committed_risk_usd = risk.compute_open_risk_usd(open_positions)
    if is_sr:
        plan = support_resistance.compute_position_plan(
            price, atr, (sr_result or {}).get("support_zone"),
            (sr_result or {}).get("resistance_zone"),
            resistance_targets=(sr_result or {}).get("resistance_targets"),
            already_committed_risk_usd=already_committed_risk_usd,
            account_equity_usd=state_store.get_current_equity_usd(),
        )
    else:
        plan = risk.compute_position_plan(
            price, atr, already_committed_risk_usd=already_committed_risk_usd,
            account_equity_usd=state_store.get_current_equity_usd(),
        )
    # Cost gate: biến động quá nhỏ so với chi phí giao dịch (hoặc ngân sách rủi ro
    # danh mục đã bị các vị thế đang mở chiếm hết) thì lệnh lỗ ngay cả khi chạm
    # đúng Take Profit — huỷ tín hiệu ở đây thay vì để Risk Engine cấp size cho
    # một lệnh không thể lãi.
    if not plan["edge_viable"]:
        rejection = {
            "reason": plan["skip_reason"], "total_score": total_score,
            "gate": plan.get("reject_gate", "cost_gate"),
        }
        for key in (
            "tp_distance_pct", "round_trip_cost_pct", "risk_reward",
            "target_candidates_considered", "min_reward_cost",
            "min_reward_rr", "best_available_reward",
        ):
            if key in plan:
                rejection[key] = plan[key]
        state_store.log_event("RISK_REJECTED", rejection)
        return "IGNORE", plan["skip_reason"], False, pullback_ok

    state_store.log_event("SIGNAL_GENERATED", {
        "decision": "BUY", "reason": reason, "total_score": total_score,
        "position_plan": {
            "stop_price": plan["stop_price"],
            "take_profit_price": plan["take_profit_price"],
            "tp_reason": plan.get("tp_reason"),
        },
    })

    entry_time = datetime.now(timezone.utc).isoformat()
    trade_id = state_store.open_position(
        symbol=config.SYMBOL,
        entry_price=price,
        entry_time=entry_time,
        entry_score=total_score,
        stop_price=plan["stop_price"],
        take_profit_price=plan["take_profit_price"],
        size_usd=plan["size_usd"],
        tp_reason=plan.get("tp_reason"),
        scoring_profile=config.SCORING_PROFILE,
        position_meta={
            "experiment_id": config.SR_EXPERIMENT_ID if is_sr else None,
            "support_resistance": sr_result if is_sr else None,
            "risk_plan": plan,
        },
    )
    state_store.log_event(
        "ENTRY",
        {
            "market": {"price": price},
            "feature": raw_features,
            # total_score/layer_scores tại đúng lúc ENTRY — trước đây chỉ có ở
            # SCORE_COMPUTED (không trade_id, phải join theo timestamp mới truy
            # ra được điểm lúc vào lệnh). Cần để phân tích tương quan layer-score
            # vs kết quả trade thật (`TODO-SIX-LAYERS`) và calibrate MTF.
            "total_score": total_score,
            "layer_scores": layer_scores,
            "risk": {
                "position_usd": plan["size_usd"],
                "sl": plan["stop_price"],
                "tp": plan["take_profit_price"],
            },
            "model": config.STRATEGY_PACKAGE_ID,
            "scoring_profile": config.SCORING_PROFILE,
            "experiment_id": config.SR_EXPERIMENT_ID if is_sr else None,
        },
        trade_id=trade_id,
    )
    report = ai_report.generate_report_cached(
        f"BUY:{trade_id}", config.SYMBOL, "BUY", price, total_score, layer_scores,
        f"{reason}. Stop {plan['stop_price']}, Target {plan['take_profit_price']}, "
        f"Size ${plan['size_usd']}",
    )
    _notify(report)
    return action, reason, True, pullback_ok


def _run_once(lock_owner=None):
    exchange = market.get_exchange()

    try:
        raw_ohlcv = market.fetch_ohlcv_multi_tf(exchange, EXCHANGE_SYMBOL, TIMEFRAMES, limit=250)
        order_book = market.fetch_order_book(exchange, EXCHANGE_SYMBOL)
        trades = market.fetch_recent_trades(exchange, EXCHANGE_SYMBOL)
    except Exception as e:
        state_store.log_event("MARKET_FETCH_FAILED", {"error": str(e)})
        print(f"Fetch dữ liệu lỗi, bỏ qua lần chạy này: {e}", file=sys.stderr)
        return False

    # Log step fetch — debug nhanh khi data bất thường (vd order book rỗng,
    # thiếu 1 khung MTF) mà không cần lục lại stdout/log file.
    state_store.log_event("MARKET_FETCHED", {
        "ohlcv_bars_by_tf": {tf: len(o) for tf, o in raw_ohlcv.items()},
        "order_book_bids": len(order_book.get("bids", [])),
        "order_book_asks": len(order_book.get("asks", [])),
        "trades_count": len(trades),
    })

    funding_rate = market.fetch_funding_rate(exchange, DERIVATIVES_SYMBOL)
    open_interest = market.fetch_open_interest(exchange, DERIVATIVES_SYMBOL)
    cross_market_changes = crossmarket.fetch_cross_market_changes()
    fear_greed = sentiment_data.fetch_fear_greed()

    df_by_tf = {tf: technical.to_dataframe(o) for tf, o in raw_ohlcv.items()}
    tech_result = technical.compute_technical_score(df_by_tf, primary_tf=PRIMARY_TF)
    snapshot_price = tech_result["last_price"]

    primary_raw_df = df_by_tf[PRIMARY_TF]
    primary_with_indicators = technical.add_indicators(primary_raw_df)
    regime_result = regime_engine.classify_regime(primary_with_indicators)
    mtf_agreement_ratio = tech_result["raw"]["mtf_agreement_ratio"]
    sr_result = support_resistance.score(
        primary_with_indicators, snapshot_price, decision_idx=-2,
    )
    sr_diagnostics = {
        "support": support_resistance.zone_diagnostics(
            primary_with_indicators, kind="low", decision_idx=-2,
        ),
        "resistance": support_resistance.zone_diagnostics(
            primary_with_indicators, kind="high", decision_idx=-2,
        ),
    }

    # CVD ưu tiên lấy từ collector_ws.py (WS thật) thay REST snapshot khi có sẵn
    ws_cvd = state_store.get_ws_cvd(config.SYMBOL)
    order_flow_result = orderflow.compute_order_flow_score(order_book, trades, ws_cvd=ws_cvd)
    derivatives_result = derivatives.compute_derivatives_score(funding_rate, open_interest, snapshot_price)
    sentiment_result = sentiment_score.compute_sentiment_score(fear_greed)
    cross_market_result = crossmarket_score.compute_cross_market_score(cross_market_changes)

    if config.SCORING_PROFILE == "support_resistance_only":
        layer_scores, scoring_weights = _sr_scoring_view(sr_result["buy_score"])
        total_score = sr_result["buy_score"]
        technical_breakdown_for_score = {key: 0.0 for key in tech_result["breakdown"]}
    else:
        layer_scores = {
            "technical": tech_result["total"],
            "order_flow": order_flow_result["total"],
            "derivatives": derivatives_result["total"],
            "cross_market": cross_market_result["total"],
            "sentiment": sentiment_result["total"],
            "regime": regime_result["score"],
        }
        total_score = decision.compute_total_score(layer_scores)
        scoring_weights = config.WEIGHTS
        technical_breakdown_for_score = tech_result["breakdown"]

    # Collector sàn thứ 2 (Binance) — chỉ để đối chiếu giá vào Feature Store,
    # KHÔNG tham gia layer_scores/Rule Engine. Lỗi/None không chặn pipeline chính.
    binance_price = market.fetch_cross_exchange_price(config.SYMBOL)
    binance_price_diff_pct = (
        round((binance_price - snapshot_price) / snapshot_price * 100, 4) if binance_price else None
    )

    # Feature Store + Raw Event: raw feature tách khỏi score, lưu mỗi lần chạy
    # để train Entry Model và làm nền Feature Lineage.
    raw_features = {
        "technical": tech_result["raw"],
        "order_flow": order_flow_result["raw"],
        "derivatives": derivatives_result["raw"],
        "cross_market": cross_market_result["raw"],
        "sentiment": sentiment_result["raw"],
        "regime": regime_result["raw"],
        "support_resistance": sr_result,
        "binance_cross_check": {"price": binance_price, "diff_pct_vs_primary": binance_price_diff_pct},
    }
    state_store.log_feature_snapshot(
        config.SYMBOL,
        snapshot_price,
        raw_features,
        lineage={
            "sources": {
                "execution_exchange": config.EXCHANGE_ID,
                "execution_market_type": config.MARKET_TYPE,
                "execution_symbol": EXCHANGE_SYMBOL,
                "derivatives_symbol": DERIVATIVES_SYMBOL,
                "timeframes": list(TIMEFRAMES),
            },
            "transformation_version": config.FEATURE_VERSION,
            "strategy_package_id": config.STRATEGY_PACKAGE_ID,
            "engine_version": config.RUNTIME_ENGINE_VERSION,
            "scoring_profile": config.SCORING_PROFILE,
            "experiment_id": config.SR_EXPERIMENT_ID if config.SCORING_PROFILE == "support_resistance_only" else None,
            "candle_policy": "last_closed_primary_bar_for_zone; live_tick_for_proximity",
            "cost": {"fee_pct": config.FEE_PCT, "slippage_pct": config.SLIPPAGE_PCT},
            "fill_assumption": "live_market_price_with_configured_slippage",
            "support_resistance_params": config.support_resistance_manifest(),
        },
    )
    state_store.log_event("FEATURE_UPDATED", {"total_score": total_score, "layer_scores": layer_scores})
    # Chi tiết "tính score như nào" (mục Logging, phát hiện 2026-08-05: chỉ log
    # total_score là không đủ để soi lại tại sao ra quyết định đó) — ghi rõ từng
    # thành phần góp vào total_score (breakdown Technical theo indicator, Regime
    # theo ADX/ATR) 1 lần mỗi activation, vì các số này không đổi trong cửa
    # sổ theo dõi (indicator theo nến đóng).
    state_store.log_event("SCORE_COMPUTED", {
        "total_score": total_score,
        "layer_scores": layer_scores,
        "weights": scoring_weights,
        "technical_breakdown": technical_breakdown_for_score,
        "regime": {"label": regime_result["label"], "raw": regime_result["raw"]},
        "buy_threshold": config.SR_DECISION_THRESHOLD if config.SCORING_PROFILE == "support_resistance_only" else config.BUY_SCORE_THRESHOLD,
        "watch_threshold": config.WATCH_SCORE_THRESHOLD,
        "scoring_profile": config.SCORING_PROFILE,
        "support_resistance": sr_result,
        "support_resistance_diagnostics": sr_diagnostics,
    })

    trading_halted = state_store.is_trading_halted_today()

    # Max Drawdown (Risk Engine): vượt ngưỡng thì tự bật Kill Switch, không tự tắt lại
    # — cần người kiểm tra và tắt thủ công (xem scripts/kill_switch.py) trước khi trade tiếp.
    state_store.arm_kill_switch_if_drawdown_breached()

    # Cửa sổ theo dõi liên tục. Stop Loss/Take Profit (giá so trực tiếp, trong
    # decide_exit) và vị trí giá so vùng pullback (trong decide_entry) đã nhận
    # `price` tươi mỗi poll từ trước.
    #
    # Score sống theo tick: lớp Technical (EMA/RSI/MACD/ADX/Supertrend/VWAP) được
    # TÍNH LẠI mỗi poll, coi giá tick là giá đóng cửa tạm thời của nến ĐANG hình
    # thành — dùng lại đúng `technical.add_indicators()`/`score_from_indicators()`
    # trên 1 bản copy dataframe đã chỉnh close/high/low của nến cuối, không phải
    # công thức rời rạc tự chế. `live_total_score` này (không phải total_score
    # đóng băng) được dùng để RA QUYẾT ĐỊNH BUY/SELL thật.
    #
    # 5 lớp còn lại (order_flow/derivatives/cross_market/sentiment) + regime vẫn
    # đóng băng theo lần fetch REST đầu `_run_once` — dữ liệu gốc của chúng
    # (order book, funding, tin tức...) không đổi theo tick giây, recompute mỗi
    # 5s sẽ phải gọi lại API ~60 lần/cửa sổ mà không thu được gì mới.
    #
    # CẢNH BÁO QUAN TRỌNG: đây là hành vi khác với backtest — backtest luôn dùng
    # score đóng băng theo nến đóng (không có dữ liệu tick lịch sử để mô phỏng
    # "giữa nến"). Paper Trading từ đây không còn kiểm chứng đúng chiến lược đã
    # backtest nữa — đang chạy 1 biến thể phản ứng theo tick, CHƯA được backtest
    # xác nhận. Mỗi poll đều ghi MARKET_TICK để quan sát; log_signal chỉ ghi khi
    # action đổi, tránh spam signal_log.
    #
    # Cửa sổ tự nới quá MONITOR_WINDOW_MINUTES nếu vẫn IN_POSITION khi hết giờ —
    # chỉ dừng khi lệnh đóng (SL/TP hoặc rule exit), không bỏ SL/TP không canh
    # giữa 2 activation (xem `TODO-MARKET-FRESHNESS`).
    window_deadline = time.monotonic() + config.MONITOR_WINDOW_MINUTES * 60
    last_logged_exit_status = None
    last_logged_entry_action = None

    while True:
        price_snapshot = _current_price(exchange)
        price = price_snapshot["price"]
        open_positions = state_store.get_open_positions()
        tick_pullback_ok = None

        live_df = primary_raw_df.copy()
        last_idx = live_df.index[-1]
        live_df.loc[last_idx, "close"] = price
        live_df.loc[last_idx, "high"] = max(live_df.loc[last_idx, "high"], price)
        live_df.loc[last_idx, "low"] = min(live_df.loc[last_idx, "low"], price)
        live_enriched = technical.add_indicators(live_df)
        live_tech = technical.score_from_indicators(live_enriched, idx=-1, agreement_ratio=mtf_agreement_ratio)
        live_sr = support_resistance.score_from_zones(
            price, sr_result["support_zone"], sr_result["resistance_zone"],
            sr_result["atr_current"], decision_idx=sr_result["decision_idx"],
            required_swings=sr_result["required_swings"],
            support_status=sr_result["support_status"],
            resistance_status=sr_result["resistance_status"],
            resistance_targets=sr_result["resistance_targets"],
        )
        if config.SCORING_PROFILE == "support_resistance_only":
            live_total_score = live_sr["buy_score"]
            live_score_side = "BUY_SUPPORT"
            sr_positions = [p for p in open_positions if p.get("scoring_profile") == "support_resistance_only"]
            if sr_positions:
                live_total_score = max(
                    support_resistance.breakdown_score(
                        price,
                        ((p.get("position_meta") or {}).get("support_resistance") or {}).get("support_zone"),
                        live_sr["atr_current"],
                    )
                    for p in sr_positions
                )
                live_score_side = "SELL_SUPPORT_BREAKDOWN"
            live_layer_scores, _ = _sr_scoring_view(live_total_score)
            live_technical_breakdown = {key: 0.0 for key in live_tech["breakdown"]}
        else:
            live_layer_scores = {**layer_scores, "technical": live_tech["total"]}
            live_total_score = decision.compute_total_score(live_layer_scores)
            live_technical_breakdown = live_tech["breakdown"]
            live_score_side = "CHAMPION"
        sr_monitor = _sr_monitor_snapshot(
            price, live_sr, live_score_side, live_total_score, open_positions,
        )

        # Kiểm tra exit cho TỪNG vị thế đang mở độc lập (thường chỉ 1, có thể
        # nhiều nếu MAX_CONCURRENT_POSITIONS > 1). SELL đã được log_signal ngay
        # trong _handle_exit, nhánh dưới chỉ dedup log HOLD giữa các tick.
        exit_status, exit_reason, remaining_open = None, "", 0
        if open_positions:
            exit_results = [
                _handle_exit(p, price, live_total_score, live_layer_scores, live_enriched, live_sr)
                for p in open_positions
            ]
            exited_count = sum(1 for exited, _ in exit_results if exited)
            remaining_open = len(open_positions) - exited_count
            exit_status = "SELL" if exited_count else "HOLD"
            exit_reason = "; ".join(r for _, r in exit_results if r)
            if exit_status == "SELL":
                last_logged_exit_status = "SELL"
            elif last_logged_exit_status != "HOLD":
                state_store.log_signal(config.SYMBOL, price, "HOLD", live_total_score, live_layer_scores, "Đang theo dõi vị thế mở")
                last_logged_exit_status = "HOLD"
        else:
            last_logged_exit_status = None

        # Chỉ tìm entry mới nếu còn slot (MAX_CONCURRENT_POSITIONS) sau khi đã
        # trừ các vị thế vừa exit ở trên trong cùng tick này.
        entry_action, entry_reason, entered = None, "", False
        profile_max_positions = 1 if config.SCORING_PROFILE == "support_resistance_only" else config.MAX_CONCURRENT_POSITIONS
        if remaining_open < profile_max_positions:
            entry_action, entry_reason, entered, tick_pullback_ok = _handle_entry(
                price, live_total_score, regime_result["label"], trading_halted,
                live_enriched, {**raw_features, "support_resistance": live_sr}, live_layer_scores,
                binance_price_diff_pct=binance_price_diff_pct,
                sr_result=live_sr,
            )
            if entry_action != last_logged_entry_action:
                state_store.log_signal(config.SYMBOL, price, entry_action, live_total_score, live_layer_scores, entry_reason)
                last_logged_entry_action = entry_action
        else:
            last_logged_entry_action = None

        tick_status = exit_status or entry_action
        tick_reason = exit_reason if open_positions else entry_reason
        open_positions_count = remaining_open + (1 if entered else 0)

        # Log đủ layer_scores + breakdown Technical mỗi tick (không chỉ total) —
        # phát hiện 2026-08-06: chỉ log total_score không đủ để soi lại quyết
        # định hoặc dùng làm data training (cùng lý do đã áp dụng cho
        # SCORE_COMPUTED, nay áp dụng thêm cho tick — đây là score THẬT dùng để
        # quyết định BUY/SELL, không phải bản đóng băng).
        state_store.log_event("MARKET_TICK", {
            "price": price, "open_positions_count": open_positions_count, "action": tick_status,
            "price_source": price_snapshot["source"],
            "price_timestamp": price_snapshot["timestamp"],
            "price_age_seconds": round(price_snapshot["age_seconds"], 3),
            "reason": tick_reason, "total_score": live_total_score, "confirmed_total_score": total_score,
            "layer_scores": live_layer_scores, "technical_breakdown": live_technical_breakdown,
            "regime": regime_result["label"], "pullback_ok": tick_pullback_ok,
            "scoring_profile": config.SCORING_PROFILE, "score_side": live_score_side,
            "support_resistance": live_sr, "sr_monitor": sr_monitor,
        })
        # Heartbeat phản ánh process/market loop còn sống, không đợi vị thế đóng.
        if lock_owner and not state_store.refresh_run_lock(lock_owner):
            raise RuntimeError("Mất quyền sở hữu run lock trong lúc đang chạy")
        state_store.record_run_health(ok=True)
        support_log = sr_monitor["support"]
        resistance_log = sr_monitor["resistance"]
        support_text = f"{support_log['low']}-{support_log['high']}:{support_log['relation']}" if support_log["available"] else "none"
        resistance_text = f"{resistance_log['low']}-{resistance_log['high']}:{resistance_log['relation']}" if resistance_log["available"] else "none"
        print(f"[tick] {datetime.now(timezone.utc).isoformat()} price={price} "
              f"open_positions={open_positions_count} action={tick_status} score={live_total_score} "
              f"score_side={live_score_side} atr={sr_monitor['atr_current']} "
              f"support={support_text} resistance={resistance_text} "
              f"regime={regime_result['label']} pullback_ok={tick_pullback_ok} reason=\"{tick_reason}\"", flush=True)

        # Continuous runtime lập tức bắt đầu cycle mới và refresh toàn bộ OHLCV/
        # layer. Vị thế nằm trong SQLite nên không mất khi sang cycle.
        if time.monotonic() >= window_deadline:
            break
        time.sleep(config.MONITOR_POLL_SECONDS)

    return True


if __name__ == "__main__":
    main()
