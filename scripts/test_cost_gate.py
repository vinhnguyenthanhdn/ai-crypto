"""MIN_TP_COST_RATIO: cổng chặn lệnh mà kịch bản tốt nhất vẫn không bù nổi chi phí.

`src/engine/risk.py` mở đầu bằng đúng lý do lớp này tồn tại — trên khung nhiễu,
TP thuần ATR có thể nằm gần entry hơn cả chi phí khứ hồi, nên `edge_viable=False`
nghĩa là "cấu trúc lệnh này không thể có lãi kể cả khi đúng hướng". README § Stops
khai nó là gate `cost_gate`.

**Đo trước khi viết, và chỗ trống hoá ra nằm ở tham số của các ca sẵn có.**
`edge_viable` được khẳng định 9 lần trong `test_sr_scoring.py` và
`test_max_concurrent_positions.py`, nhưng gần hết các ca đó truyền
`fee_pct=0, slippage_pct=0`: chi phí bằng 0 thì ngưỡng `cost * k` cũng bằng 0 với
**mọi** `k`, nên hệ số — thứ duy nhất lớp này đóng góp — không quan sát được. Đo
bằng 6 đột biến trên hai file đó: bỏ hẳn hệ số, đổi `>=` thành `>`, và bỏ hẳn cổng
(`edge_viable = True`) đều **xanh**; chỉ hai đột biến làm sai *chi phí* (một chiều
thay vì khứ hồi, bỏ phép đổi sang phần trăm) mới đỏ, và chúng đỏ nhờ hai ca có
fee khác 0 chứ không nhờ ca nào nhắm vào cổng này.

Kỳ vọng ở đây suy từ `config.MIN_TP_COST_RATIO` và `risk.round_trip_cost_pct()`
chứ không viết lại `2.5` hay `0.3`: một ca chép hằng số vẫn xanh sau khi ngưỡng
thật đã đổi, tức nó chấm một con số không còn ai dùng.

**Ngưỡng có hai người đọc mang hai đơn vị**, và đó là lý do ca cuối tồn tại.
`risk._plan_common` so bằng **phần trăm** (`tp_distance_pct >= cost_pct * k`), còn
`support_resistance.compute_position_plan` — đường sống của profile
`support_resistance_only` — dựng lại cùng luật bằng **giá**
(`entry * cost/100 * k`) để lọc target trước khi có TP. Hai bản viết tay của cùng
một luật là chỗ lệch không ai báo, đúng như `MAX_CONCURRENT_POSITIONS` hồi `#49`:
bỏ hệ số khỏi bản SR thì mọi test của bản `risk` vẫn xanh, trong khi profile SR
nhận mọi target chỉ vừa đủ hoà chi phí.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config  # noqa: E402
from src.engine import risk, support_resistance as sr  # noqa: E402

ENTRY = 100.0
FEE_PCT = 0.001
SLIPPAGE_PCT = 0.0005


def _ratio() -> float:
    """Cổng chỉ có nghĩa khi hệ số > 1; bằng 1 thì nó rút về 'hoà chi phí'."""
    k = float(config.MIN_TP_COST_RATIO)
    assert k > 1, f"MIN_TP_COST_RATIO={k} không lớn hơn 1, mọi ca dưới đây rỗng nghĩa"
    return k


def _cost_pct() -> float:
    return risk.round_trip_cost_pct(FEE_PCT, SLIPPAGE_PCT)


def _atr_for_tp_distance_pct(target_pct: float) -> float:
    """ATR để `compute_position_plan` đặt TP cách entry đúng `target_pct` phần trăm."""
    return target_pct / 100 * ENTRY / (config.ATR_STOP_MULTIPLIER * config.RISK_REWARD_RATIO)


def _plan(target_pct: float) -> dict:
    return risk.compute_position_plan(
        ENTRY, _atr_for_tp_distance_pct(target_pct),
        fee_pct=FEE_PCT, slippage_pct=SLIPPAGE_PCT, account_equity_usd=500,
    )


def test_a_target_past_the_cost_but_short_of_the_ratio_is_refused():
    """Cửa sổ giữa hai ngưỡng là chỗ duy nhất hệ số quan sát được.

    TP xa hơn chi phí khứ hồi — nên một cổng chỉ đòi 'hoà chi phí' sẽ nhận — nhưng
    chưa tới `cost * k`. Đây là ca giết đột biến bỏ hệ số **và** đột biến bỏ hẳn
    cổng, hai thứ mà 9 khẳng định `edge_viable` sẵn có đều để sống.
    """
    cost = _cost_pct()
    between = cost * (1 + _ratio()) / 2
    assert cost < between < cost * _ratio(), (cost, between)
    plan = _plan(between)
    assert not plan["edge_viable"], plan
    assert f"{cost * _ratio():.3f}%" in plan["skip_reason"], plan["skip_reason"]


def test_the_ratio_threshold_is_inclusive():
    """`>=`, không phải `>` — ca duy nhất phân biệt được hai cách viết.

    Khoảng cách dựng ngược từ ngưỡng nên nó bằng ngưỡng **chính xác** chứ không
    xấp xỉ; premise được khẳng định tại chỗ, vì một ca biên trượt khỏi biên thì
    nó không còn chấm biên nữa mà vẫn xanh.
    """
    threshold = _cost_pct() * _ratio()
    atr = _atr_for_tp_distance_pct(threshold)
    distance_pct = (atr * config.ATR_STOP_MULTIPLIER * config.RISK_REWARD_RATIO) / ENTRY * 100
    assert distance_pct == threshold, (distance_pct, threshold)
    plan = _plan(threshold)
    assert plan["edge_viable"], plan
    assert plan["min_tp_distance_pct"] == round(threshold, 4), plan


def test_a_hair_under_the_threshold_is_refused():
    threshold = _cost_pct() * _ratio()
    plan = _plan(threshold * (1 - 1e-9))
    assert not plan["edge_viable"], plan


def test_the_cost_side_of_the_hurdle_is_the_round_trip_read_from_config():
    """Không truyền fee/slippage thì ngưỡng phải suy từ config, và là chi phí
    **khứ hồi** — vào và ra, mỗi chiều gồm fee lẫn slippage.

    Vế này giết ba đột biến khác nhau về chi phí: mặc định fee thành 0 (ngưỡng
    tụt, cổng thôi từ chối gì), bỏ nhân 2 (nửa ngưỡng), bỏ đổi sang phần trăm.
    """
    expected_cost = (config.FEE_PCT + config.SLIPPAGE_PCT) * 2 * 100
    assert risk.round_trip_cost_pct() == expected_cost
    plan = risk.compute_position_plan(ENTRY, 1.0, account_equity_usd=500)
    assert plan["round_trip_cost_pct"] == round(expected_cost, 4), plan
    assert plan["min_tp_distance_pct"] == round(expected_cost * _ratio(), 4), plan


def test_the_short_path_uses_the_same_hurdle():
    """`compute_short_position_plan` là mirror của đường long và đi qua cùng
    `_plan_common`; một đột biến chỉ gác chiều long sẽ để lệnh short chui lọt."""
    between = _cost_pct() * (1 + _ratio()) / 2
    atr = _atr_for_tp_distance_pct(between)
    short = risk.compute_short_position_plan(
        ENTRY, atr, fee_pct=FEE_PCT, slippage_pct=SLIPPAGE_PCT, account_equity_usd=500)
    long_plan = _plan(between)
    assert short["min_tp_distance_pct"] == long_plan["min_tp_distance_pct"], (short, long_plan)
    assert not short["edge_viable"], short


def test_the_support_resistance_reader_uses_the_same_hurdle():
    """Bản viết tay thứ hai của cùng một luật, tính bằng giá thay vì phần trăm.

    `support_resistance.compute_position_plan` lọc target bằng
    `entry * round_trip_cost/100 * k` **trước khi** có TP để `_plan_common` chấm,
    nên mọi ca của bản `risk` đều xanh dù bản này mất hệ số. Ca dựng đúng cửa sổ
    giữa hai ngưỡng: target hoà được chi phí nhưng chưa đạt `k` lần chi phí.

    ATR chọn đủ lớn để target không rơi vào nhánh `SR_FAR_RESISTANCE_ATR` (nhánh
    đó đổi TP sang Fibonacci và không còn quan sát được ngưỡng chi phí), và
    support đặt sát entry để `SR_MIN_RISK_REWARD` không phải vế ràng buộc — nếu
    không thì ca này chấm ngưỡng R:R chứ không chấm ngưỡng chi phí.
    """
    cost_reward = ENTRY * _cost_pct() / 100
    hurdle = cost_reward * _ratio()
    atr = 0.5
    support = {"low": 99.9, "high": 99.95, "touch_count": 2, "swings": []}
    stop_distance = ENTRY - (support["low"] - config.SR_SL_BUFFER_ATR * atr)
    assert stop_distance * config.SR_MIN_RISK_REWARD < hurdle, stop_distance
    assert (hurdle / atr) <= config.SR_FAR_RESISTANCE_ATR, hurdle / atr

    def plan_for(reward: float) -> dict:
        target = {"low": ENTRY + reward, "high": ENTRY + reward + 0.01,
                  "touch_count": 2, "swings": []}
        return sr.compute_position_plan(
            ENTRY, atr, support, target,
            fee_pct=FEE_PCT, slippage_pct=SLIPPAGE_PCT, account_equity_usd=500,
        )

    between = cost_reward * (1 + _ratio()) / 2
    assert cost_reward < between < hurdle, (cost_reward, between, hurdle)
    refused = plan_for(between)
    assert not refused["edge_viable"], refused
    assert refused["reject_gate"] == "target_not_viable", refused

    accepted = plan_for(hurdle)
    assert accepted["edge_viable"], accepted
    assert accepted["tp_reason"] == "TAKE_PROFIT_DIRECT_HIGH", accepted


def main():
    tests = [
        test_a_target_past_the_cost_but_short_of_the_ratio_is_refused,
        test_the_ratio_threshold_is_inclusive,
        test_a_hair_under_the_threshold_is_refused,
        test_the_cost_side_of_the_hurdle_is_the_round_trip_read_from_config,
        test_the_short_path_uses_the_same_hurdle,
        test_the_support_resistance_reader_uses_the_same_hurdle,
    ]
    for test in tests:
        test()
        print(f"ok   {test.__name__}")
    print(f"{len(tests)} passed")


if __name__ == "__main__":
    main()
