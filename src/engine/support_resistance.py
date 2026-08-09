"""Support/Resistance-only feature, score và position plan.

Mọi swing chỉ được dùng sau khi có đủ right-side bars. Zone/ATR formation được
đóng băng từ dữ liệu đã đóng; current price chỉ ảnh hưởng proximity score.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import combinations
from statistics import median

import pandas as pd

from .. import config
from . import risk


@dataclass(frozen=True)
class Swing:
    idx: int
    ts: str
    price: float
    atr: float


@dataclass(frozen=True)
class Zone:
    kind: str
    low: float
    high: float
    atr_form: float
    swings: tuple[Swing, ...]

    @property
    def touch_count(self) -> int:
        return len(self.swings)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["touch_count"] = self.touch_count
        return data


def _normalize_idx(n: int, idx: int) -> int:
    return idx if idx >= 0 else n + idx


def confirmed_swings(
    enriched: pd.DataFrame,
    *,
    kind: str,
    decision_idx: int = -1,
    window: int | None = None,
    lookback: int | None = None,
) -> list[Swing]:
    """Trả swing tăng dần theo thời gian, chỉ tới bar đã confirm causal."""
    if kind not in ("low", "high"):
        raise ValueError("kind phải là low hoặc high")
    window = config.SR_SWING_WINDOW if window is None else window
    lookback = config.SR_SWING_LOOKBACK if lookback is None else lookback
    n = len(enriched)
    end = _normalize_idx(n, decision_idx)
    if end < 0 or window < 1:
        return []
    field = "low" if kind == "low" else "high"
    values = enriched[field].to_numpy()
    start = max(window, end - lookback + 1)
    last_candidate = end - window
    result = []
    for i in range(start, last_candidate + 1):
        if i - window < 0 or i + window > end:
            continue
        value = float(values[i])
        left = values[i - window:i]
        right = values[i + 1:i + window + 1]
        is_swing = value < min(left.min(), right.min()) if kind == "low" else value > max(left.max(), right.max())
        atr = enriched.iloc[i].get("atr")
        if is_swing and atr is not None and not pd.isna(atr) and float(atr) > 0:
            ts = enriched.iloc[i].get("ts", i)
            result.append(Swing(i, str(ts), value, float(atr)))
    return result


def _zone_unbroken(enriched: pd.DataFrame, kind: str, first_idx: int, decision_idx: int,
                   boundary: float, buffer: float) -> bool:
    closes = enriched["close"].iloc[first_idx + 1:decision_idx + 1]
    if closes.empty:
        return True
    if kind == "low":
        return bool((closes >= boundary - buffer).all())
    return bool((closes <= boundary + buffer).all())


def find_active_zone(
    enriched: pd.DataFrame,
    *,
    kind: str,
    decision_idx: int = -1,
    required_swings: int | None = None,
) -> Zone | None:
    """Tìm zone active gần nhất. Candidate pair phải tách confirmation window,
    cùng biên ATR và không bị close phá từ touch đầu tới decision time."""
    required_swings = config.SR_REQUIRED_SWINGS if required_swings is None else required_swings
    required_swings = max(1, required_swings)
    end = _normalize_idx(len(enriched), decision_idx)
    swings = confirmed_swings(enriched, kind=kind, decision_idx=end)
    if not swings:
        return None
    min_separation = config.SR_SWING_WINDOW * 2 + 1

    # Thử từ swing mới nhất; mỗi candidate gom thêm touch cũ độc lập cùng zone.
    for newest_pos in range(len(swings) - 1, -1, -1):
        selected = [swings[newest_pos]]
        # required=1 là negative-control đăng ký trước: chỉ một swing gần nhất,
        # không âm thầm gom thêm touch để score vượt mức tối đa 50.
        if required_swings > 1:
            for candidate in reversed(swings[:newest_pos]):
                if any(abs(candidate.idx - s.idx) < min_separation for s in selected):
                    continue
                trial = selected + [candidate]
                atr_form = float(median(s.atr for s in trial))
                prices = [s.price for s in trial]
                if max(prices) - min(prices) <= config.SR_SAME_ZONE_MAX_SPREAD_ATR * atr_form:
                    selected.append(candidate)

        if len(selected) < required_swings:
            continue
        selected.sort(key=lambda s: s.idx)
        atr_form = float(median(s.atr for s in selected))
        prices = [s.price for s in selected]
        zone_low, zone_high = min(prices), max(prices)
        boundary = zone_low if kind == "low" else zone_high
        if not _zone_unbroken(
            enriched, kind, selected[0].idx, end, boundary,
            config.SR_INVALIDATION_CLOSE_ATR * atr_form,
        ):
            continue
        return Zone(kind, zone_low, zone_high, atr_form, tuple(selected))
    return None


def active_single_swing_zones(
    enriched: pd.DataFrame,
    *,
    kind: str,
    decision_idx: int = -1,
) -> list[Zone]:
    """Mọi confirmed swing đơn còn hiệu lực, dùng làm danh sách target phụ.

    Với resistance, high đã từng bị close phá quá invalidation buffer không còn
    là target hợp lệ. Danh sách này không tham gia BUY score.
    """
    end = _normalize_idx(len(enriched), decision_idx)
    result = []
    for swing in confirmed_swings(enriched, kind=kind, decision_idx=end):
        if _zone_unbroken(
            enriched, kind, swing.idx, end, swing.price,
            config.SR_INVALIDATION_CLOSE_ATR * swing.atr,
        ):
            result.append(Zone(kind, swing.price, swing.price, swing.atr, (swing,)))
    return sorted(result, key=lambda zone: zone.low)


def zone_diagnostics(enriched: pd.DataFrame, *, kind: str,
                     decision_idx: int = -1,
                     required_swings: int | None = None,
                     max_pairs: int = 8) -> dict:
    """Giải thích vì sao có/không có active zone mà không thay đổi scoring."""
    required = config.SR_REQUIRED_SWINGS if required_swings is None else max(1, required_swings)
    end = _normalize_idx(len(enriched), decision_idx)
    swings = confirmed_swings(enriched, kind=kind, decision_idx=end)
    min_separation = config.SR_SWING_WINDOW * 2 + 1
    pair_rows = []
    for first, second in combinations(swings, 2):
        atr_form = float(median((first.atr, second.atr)))
        spread = abs(first.price - second.price)
        allowed = config.SR_SAME_ZONE_MAX_SPREAD_ATR * atr_form
        independent = abs(second.idx - first.idx) >= min_separation
        same_zone = spread <= allowed
        zone_low, zone_high = min(first.price, second.price), max(first.price, second.price)
        boundary = zone_low if kind == "low" else zone_high
        invalidation_level = (
            boundary - config.SR_INVALIDATION_CLOSE_ATR * atr_form
            if kind == "low"
            else boundary + config.SR_INVALIDATION_CLOSE_ATR * atr_form
        )
        closes = enriched["close"].iloc[first.idx + 1:end + 1]
        extreme_close = (
            float(closes.min()) if kind == "low" and not closes.empty
            else float(closes.max()) if not closes.empty
            else None
        )
        unbroken = _zone_unbroken(
            enriched, kind, first.idx, end, boundary,
            config.SR_INVALIDATION_CLOSE_ATR * atr_form,
        )
        reasons = []
        if not independent:
            reasons.append("NOT_INDEPENDENT")
        if not same_zone:
            reasons.append("TOO_WIDE")
        if not unbroken:
            reasons.append("BROKEN")
        pair_rows.append({
            "first": asdict(first), "second": asdict(second),
            "spread_usd": round(spread, 8),
            "allowed_spread_usd": round(allowed, 8),
            "spread_atr": round(spread / atr_form, 6) if atr_form else None,
            "independent": independent,
            "same_zone": same_zone,
            "unbroken": unbroken,
            "invalidation_level": round(invalidation_level, 8),
            "extreme_close_after_first": extreme_close,
            "eligible": not reasons,
            "reasons": reasons,
        })
    # Cặp gần điều kiện spread nhất trước; pair cùng zone nhưng bị break sẽ nổi
    # lên đầu để dashboard giải thích trường hợp tưởng như phải có zone.
    pair_rows.sort(key=lambda row: (
        not row["same_zone"],
        row["spread_usd"] / row["allowed_spread_usd"] if row["allowed_spread_usd"] else float("inf"),
    ))
    active = find_active_zone(
        enriched, kind=kind, decision_idx=end, required_swings=required,
    )
    if active:
        summary = "ACTIVE_ZONE"
    elif not swings:
        summary = "NO_CONFIRMED_SWING"
    elif len(swings) < required:
        summary = f"NEED_{required}_SWINGS_HAVE_{len(swings)}"
    elif any(row["same_zone"] and not row["unbroken"] for row in pair_rows):
        summary = "CLOSEST_SAME_ZONE_WAS_BROKEN"
    elif pair_rows:
        summary = "NO_PAIR_WITHIN_SPREAD"
    else:
        summary = "NO_ELIGIBLE_ZONE"
    return {
        "kind": kind,
        "decision_idx": end,
        "required_swings": required,
        "confirmed_swing_count": len(swings),
        "confirmed_swings": [asdict(swing) for swing in swings],
        "active_zone": active.to_dict() if active else None,
        "summary": summary,
        "pairs_shown": pair_rows[:max_pairs],
        "total_pair_count": len(pair_rows),
        "rules": {
            "same_zone_max_spread_atr": config.SR_SAME_ZONE_MAX_SPREAD_ATR,
            "invalidation_close_atr": config.SR_INVALIDATION_CLOSE_ATR,
            "minimum_bar_separation": min_separation,
        },
    }


def zone_quality(touch_count: int) -> float:
    if touch_count <= 1:
        return config.SR_ZONE_QUALITY_1
    if touch_count == 2:
        return config.SR_ZONE_QUALITY_2
    if touch_count == 3:
        return config.SR_ZONE_QUALITY_3
    return config.SR_ZONE_QUALITY_4_PLUS


def _zone_value(zone: dict | Zone, key: str):
    return getattr(zone, key) if isinstance(zone, Zone) else zone[key]


def _proximity(price: float, zone: dict | Zone, atr_current: float) -> float:
    if atr_current <= 0:
        return 0.0
    low, high, kind = _zone_value(zone, "low"), _zone_value(zone, "high"), _zone_value(zone, "kind")
    if low <= price <= high:
        return 1.0
    if kind == "low":
        distance = low - price if price < low else price - high
    else:
        if price > high:
            return 0.0
        distance = low - price
    width = config.SR_APPROACH_WIDTH_ATR * atr_current
    if width <= 0:
        return 0.0
    # Hyperbolic không bị ép sát 0 quá nhanh như exponential, nên score vẫn phản
    # ứng rõ khi giá còn cách support 1-3 ATR. Hiệu chỉnh để confirmed 2-touch
    # zone có proximity=threshold/100 đúng tại khoảng cách 0.09 ATR.
    threshold_ratio = max(1e-9, min(0.999999, config.SR_DECISION_THRESHOLD / 100))
    calibration_ratio = config.SR_BUY_THRESHOLD_DISTANCE_ATR / config.SR_APPROACH_WIDTH_ATR
    sensitivity = ((1 / threshold_ratio) - 1) / calibration_ratio if calibration_ratio > 0 else 1.0
    return 1 / (1 + sensitivity * distance / width)


def breakdown_score(current_price: float, support_zone: dict | Zone | None,
                    atr_current: float) -> float:
    """SELL score của Long chỉ đo mức phá xuống dưới support của lệnh.

    Chạm/trùng đáy không tạo SELL. Vùng xuyên tối đa fake-break buffer vẫn 0;
    score tăng tuyến tính từ fake-break tới close-invalidation buffer và đạt
    100 ở biên invalidation. Caller đang giữ lệnh phải truyền support zone đã
    đóng băng tại entry để zone không biến mất khỏi feature sau khi bị phá.
    """
    if support_zone is None or atr_current <= 0:
        return 0.0
    support = support_zone.to_dict() if isinstance(support_zone, Zone) else support_zone
    distance_atr = (float(support["low"]) - current_price) / atr_current
    start = config.SR_FAKE_BREAK_WICK_ATR
    end = config.SR_INVALIDATION_CLOSE_ATR
    if distance_atr <= start:
        return 0.0
    if end <= start:
        return 100.0
    return round(100 * min(1.0, (distance_atr - start) / (end - start)), 2)


def score(
    enriched: pd.DataFrame,
    current_price: float,
    *,
    decision_idx: int = -1,
    required_swings: int | None = None,
) -> dict:
    """BUY từ retest support; SELL từ breakdown support, không từ swing high."""
    end = _normalize_idx(len(enriched), decision_idx)
    atr = enriched.iloc[end].get("atr") if end >= 0 else None
    atr_current = 0.0 if atr is None or pd.isna(atr) else float(atr)
    support = find_active_zone(
        enriched, kind="low", decision_idx=end, required_swings=required_swings,
    )
    required = config.SR_REQUIRED_SWINGS if required_swings is None else max(1, required_swings)
    support_status = "CONFIRMED_ZONE" if support is not None else "NO_SUPPORT"
    if support is None and required > 1:
        support = find_active_zone(
            enriched, kind="low", decision_idx=end, required_swings=1,
        )
        if support is not None:
            support_status = "SINGLE_SWING_CANDIDATE"
    resistance = find_active_zone(
        enriched, kind="high", decision_idx=end, required_swings=required_swings,
    )
    resistance_status = "CONFIRMED_ZONE" if resistance is not None else "NO_RESISTANCE"
    if resistance is None and required > 1:
        resistance = find_active_zone(
            enriched, kind="high", decision_idx=end, required_swings=1,
        )
        if resistance is not None:
            resistance_status = "SINGLE_SWING_TARGET"
    resistance_targets = active_single_swing_zones(
        enriched, kind="high", decision_idx=end,
    )
    return score_from_zones(
        current_price, support, resistance, atr_current, decision_idx=end,
        required_swings=required, support_status=support_status,
        resistance_status=resistance_status,
        resistance_targets=resistance_targets,
    )


def score_from_zones(current_price: float, support: dict | Zone | None,
                     resistance: dict | Zone | None, atr_current: float,
                     *, decision_idx: int | None = None,
                     required_swings: int | None = None,
                     support_status: str | None = None,
                     resistance_status: str | None = None,
                     resistance_targets: list[dict | Zone] | None = None) -> dict:
    """Reprice snapshot zone đã dựng causal; zone chỉ đổi khi primary bar đổi."""
    support_dict = support.to_dict() if isinstance(support, Zone) else support
    resistance_dict = resistance.to_dict() if isinstance(resistance, Zone) else resistance
    target_dicts = [
        target.to_dict() if isinstance(target, Zone) else target
        for target in (resistance_targets or [])
    ]
    touches = int((support_dict or {}).get("touch_count", 0))
    raw_buy = 0.0 if support_dict is None else 100 * _proximity(current_price, support_dict, atr_current) * zone_quality(touches)
    buy = max(config.SR_SCORE_FLOOR, raw_buy)
    sell = breakdown_score(current_price, support_dict, atr_current)
    required = config.SR_REQUIRED_SWINGS if required_swings is None else max(1, required_swings)
    below_support = bool(support_dict and current_price < float(support_dict["low"]))
    enough_swings = touches >= required
    buy_eligible = bool(support_dict) and enough_swings and not below_support
    if support_status is None:
        support_status = (
            "NO_SUPPORT" if support_dict is None
            else ("CONFIRMED_ZONE" if enough_swings else "SINGLE_SWING_CANDIDATE")
        )
    if resistance_status is None:
        resistance_status = "NO_RESISTANCE" if resistance_dict is None else (
            "CONFIRMED_ZONE" if int(resistance_dict.get("touch_count", 0)) >= required
            else "SINGLE_SWING_TARGET"
        )
    threshold = config.SR_DECISION_THRESHOLD
    conflict = buy >= threshold and sell >= threshold
    return {
        "buy_score": round(buy, 2),
        "sell_score": round(sell, 2),
        "conflict": conflict,
        "support_zone": support_dict,
        "resistance_zone": resistance_dict,
        "resistance_targets": target_dicts,
        "atr_current": round(atr_current, 8),
        "sell_basis": "support_breakdown",
        "support_status": support_status,
        "resistance_status": resistance_status,
        "buy_eligible": buy_eligible,
        "buy_ineligible_reason": (
            "NO_SUPPORT" if support_dict is None
            else ("NEED_MORE_SWINGS" if not enough_swings else ("WAIT_RECLAIM" if below_support else None))
        ),
        "required_swings": required,
        "decision_idx": decision_idx,
        "threshold": threshold,
    }


def compute_position_plan(
    entry_price: float,
    atr_entry: float,
    support_zone: dict | Zone | None,
    resistance_zone: dict | Zone | None,
    *,
    resistance_targets: list[dict | Zone] | None = None,
    fee_pct: float | None = None,
    slippage_pct: float | None = None,
    already_committed_risk_usd: float = 0.0,
    account_equity_usd: float | None = None,
) -> dict:
    """SL theo support thấp hơn; TP direct high hoặc Fibonacci trung gian."""
    if support_zone is None:
        return {"edge_viable": False, "reject_gate": "missing_support", "skip_reason": "Thiếu support zone hợp lệ"}
    if resistance_zone is None and not resistance_targets:
        return {"edge_viable": False, "reject_gate": "missing_resistance", "skip_reason": "Thiếu resistance target hợp lệ"}
    if atr_entry <= 0:
        return {"edge_viable": False, "reject_gate": "invalid_atr", "skip_reason": "ATR không hợp lệ"}
    support = support_zone.to_dict() if isinstance(support_zone, Zone) else support_zone
    zone_low = float(support["low"])
    stop_price = zone_low - config.SR_SL_BUFFER_ATR * atr_entry
    if stop_price >= entry_price:
        return {"edge_viable": False, "reject_gate": "invalid_structure", "skip_reason": "Support không nằm dưới entry hợp lệ"}

    raw_targets = ([resistance_zone] if resistance_zone is not None else []) + list(resistance_targets or [])
    candidates = []
    seen = set()
    for raw_target in raw_targets:
        target = raw_target.to_dict() if isinstance(raw_target, Zone) else raw_target
        key = (float(target["low"]), float(target["high"]))
        if key in seen or float(target["low"]) <= entry_price:
            continue
        seen.add(key)
        candidates.append(target)
    candidates.sort(key=lambda target: float(target["low"]))
    if not candidates:
        return {"edge_viable": False, "reject_gate": "invalid_structure", "skip_reason": "Không có resistance target nằm trên entry"}

    stop_distance = entry_price - stop_price
    min_reward_cost = (
        entry_price * risk.round_trip_cost_pct(fee_pct, slippage_pct) / 100
        * config.MIN_TP_COST_RATIO
    )
    min_reward_rr = stop_distance * config.SR_MIN_RISK_REWARD
    min_reward = max(min_reward_cost, min_reward_rr)
    take_profit = None
    resistance = None
    tp_reason = None
    fib_level = None
    selected_rank = None
    best_available_reward = 0.0
    for rank, target in enumerate(candidates, start=1):
        direct_tp = float(target["low"])
        far = (direct_tp - entry_price) / atr_entry > config.SR_FAR_RESISTANCE_ATR
        if not far:
            reward = direct_tp - entry_price
            best_available_reward = max(best_available_reward, reward)
            if reward >= min_reward:
                take_profit, resistance = direct_tp, target
                tp_reason, selected_rank = "TAKE_PROFIT_DIRECT_HIGH", rank
                break
            continue
        span = direct_tp - zone_low
        for level in config.SR_FIB_LEVELS:
            candidate_price = zone_low + level * span
            reward = candidate_price - entry_price
            best_available_reward = max(best_available_reward, reward)
            if candidate_price > entry_price and reward >= min_reward:
                take_profit, resistance = candidate_price, target
                tp_reason, fib_level, selected_rank = "TAKE_PROFIT_FIB", level, rank
                break
        if take_profit is not None:
            break
    if take_profit is None:
        return {
            "edge_viable": False,
            "reject_gate": "target_not_viable",
            "skip_reason": "Không có TP direct/Fibonacci thỏa cost và minimum R:R",
            "target_candidates_considered": len(candidates),
            "min_reward_cost": round(min_reward_cost, 8),
            "min_reward_rr": round(min_reward_rr, 8),
            "best_available_reward": round(best_available_reward, 8),
        }

    plan = risk._plan_common(
        entry_price, stop_price, take_profit, fee_pct, slippage_pct,
        already_committed_risk_usd=already_committed_risk_usd, side="long",
        account_equity_usd=account_equity_usd,
    )
    plan.update({
        "tp_reason": tp_reason,
        "fib_level": fib_level,
        "support_zone": support,
        "resistance_zone": resistance,
        "target_candidates_considered": len(candidates),
        "selected_resistance_rank": selected_rank,
        "risk_reward": round((take_profit - entry_price) / stop_distance, 6),
    })
    return plan
