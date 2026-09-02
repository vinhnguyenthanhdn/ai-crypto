"""MAX_CONCURRENT_POSITIONS: lớp bảo vệ cuối chưa có cổng, và nó có hai người đọc.

`README.md` § Stops khai `max_concurrent_positions` là một gate vào lệnh, và
`run.py:_handle_entry` áp nó bằng `len(get_open_positions()) >= trần`. Nhưng
cùng con số đó có **người đọc thứ hai mang nghĩa khác**: `_plan_common` nhân nó
với rủi ro trên mỗi lệnh để ra ngân sách rủi ro toàn danh mục, rồi từ chối size
một lệnh mới khi ngân sách đã bị các lệnh đang mở chiếm hết. Một trần đếm và một
trần tiền, cùng một hằng số.

Trước file này trần đó được viết lại ở **ba** chỗ độc lập, và chỗ thứ ba lệch:

- `run.py:322` (cổng đếm trong `_handle_entry`) và `run.py:659` (tiền kiểm trên
  `remaining_open` ở vòng chính) mỗi chỗ tự viết `1 if <profile SR> else config…`
  — hai bản chép của cùng một biểu thức, đúng lớp lệch mà repo này đã gặp ở mọi
  cặp danh sách chép tay khác.
- `risk.py` (ngân sách rủi ro) đọc thẳng `config.MAX_CONCURRENT_POSITIONS` và
  **bỏ hẳn vế profile**. Dưới profile `support_resistance_only` với cấu hình lớn
  hơn 1, cổng đếm cấp một slot trong khi ngân sách cấp cho nhiều — nên lớp ngân
  sách không còn ràng buộc gì, và không có gì báo: nó chỉ thôi đỏ.

Cả ba giờ đi qua `risk.max_concurrent_positions()`. Ca thứ ba dưới đây là cổng
chống drift: nó đòi `run.py` — file duy nhất *áp* trần — không còn đọc hằng số
trực tiếp, nên một bản chép thứ tư không lặng lẽ mọc lại được.

Trần mặc định là `1`, nên **mọi ca ở đây tự đặt cấu hình của nó** thay vì tin
vào mặc định: ở `1`, một trần đếm và một trần bị bỏ qua cho cùng kết quả, và đó
đúng là lý do chỗ lệch sống lâu.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config  # noqa: E402
from src.engine import risk  # noqa: E402

_SR = "support_resistance_only"


def _with_config(profile, cap, fn):
    """Đặt profile/trần quanh đúng một phép đo rồi trả lại nguyên trạng."""
    old_profile, old_cap = config.SCORING_PROFILE, config.MAX_CONCURRENT_POSITIONS
    config.SCORING_PROFILE, config.MAX_CONCURRENT_POSITIONS = profile, cap
    try:
        return fn()
    finally:
        config.SCORING_PROFILE, config.MAX_CONCURRENT_POSITIONS = old_profile, old_cap


def test_the_default_profile_uses_the_configured_cap():
    """Trần phải là giá trị đang cấu hình, không phải một hằng số trong mã."""
    assert _with_config("champion", 3, risk.max_concurrent_positions) == 3
    assert _with_config("champion", 1, risk.max_concurrent_positions) == 1


def test_the_sr_profile_is_pinned_to_one_slot():
    """Profile SR vào lệnh theo một vùng support; lệnh thứ hai là cùng luận điểm
    đặt hai lần. Trần của nó không theo cấu hình, và ca này là chỗ duy nhất phân
    biệt hai profile — chạy ở trần 1 thì cả hai cho cùng số."""
    assert _with_config(_SR, 3, risk.max_concurrent_positions) == 1
    assert _with_config("champion", 3, risk.max_concurrent_positions) == 3

    # Tham số tường minh phải thắng config, vì caller nào cũng đọc được trần của
    # một profile không phải profile đang chạy.
    assert _with_config("champion", 3, lambda: risk.max_concurrent_positions(_SR)) == 1


def test_the_entry_gate_reads_the_cap_through_one_function():
    """Cổng chống drift, và nó hỏi một tính chất thay vì liệt kê dòng.

    `run.py` là file duy nhất *áp* trần (cổng đếm cộng tiền kiểm ở vòng chính).
    Nó không được đọc `config.MAX_CONCURRENT_POSITIONS` trực tiếp: mỗi lần đọc
    trực tiếp là một bản chép của vế profile, và hai bản chép đã tồn tại ở đây
    suốt nhiều tuần mà mọi check vẫn xanh. Khẳng định rỗng-là-lỗi đi kèm — nếu
    `run.py` thôi gọi hàm này thì cổng phải đỏ, không phải thành no-op im lặng.
    """
    source = (Path(__file__).resolve().parents[1] / "src" / "run.py").read_text()

    assert "config.MAX_CONCURRENT_POSITIONS" not in source, (
        "run.py đọc trần trực tiếp — đó là một bản chép của vế profile"
    )
    assert source.count("risk.max_concurrent_positions()") >= 2, (
        "cả hai điểm quyết định của run.py phải đi qua đúng một hàm"
    )


def test_the_risk_budget_scales_with_the_same_cap_the_gate_counts():
    """Vế đã lệch thật, và nó chỉ quan sát được ở trần > 1 dưới profile SR.

    Ngân sách rủi ro danh mục = rủi ro/lệnh x trần. Với `already_committed`
    ngay trên mức một lệnh, ngân sách của một slot đã hết nên lệnh mới size 0 và
    `edge_viable` False; ngân sách của ba slot thì còn dư và lệnh vẫn vào. Trước
    khi cả ba người đọc dùng chung một hàm, đúng cấu hình này cho cổng đếm nói
    "một slot" và ngân sách nói "ba" — hai phán quyết ngược nhau về cùng một tick.
    """
    per_trade_risk = config.ACCOUNT_EQUITY_USD * (config.RISK_PER_TRADE_PCT / 100)
    committed = per_trade_risk + 1

    def plan():
        return risk.compute_position_plan(
            100.0, 2.0, already_committed_risk_usd=committed,
        )

    sr = _with_config(_SR, 3, plan)
    assert not sr["edge_viable"], sr
    assert sr["size_usd"] == 0.0, sr
    # `max(0.0, …)` là chỗ duy nhất kẹp phần ngân sách còn lại. Bỏ nó thì
    # `size_usd` vẫn 0 (một số âm cũng không size được lệnh nào), nên phán quyết
    # không đổi và đột biến sống — nhưng kế hoạch trả về mang `risk_amount_usd`
    # âm, tức một lời khai sai về số tiền đang được đặt vào lệnh, ở đúng trường
    # mà dashboard và sổ đọc.
    assert sr["risk_amount_usd"] == 0.0, sr
    assert "Ngân sách rủi ro danh mục" in sr["skip_reason"], sr["skip_reason"]
    assert f"${round(per_trade_risk, 2)}" in sr["skip_reason"], sr["skip_reason"]

    champion = _with_config("champion", 3, plan)
    assert champion["edge_viable"], champion
    assert champion["size_usd"] > 0, champion


def test_the_cap_is_a_whole_number_of_slots():
    """Trần là số slot, và `run.py` so nó với `len(...)`.

    Một trần lấy từ môi trường có thể là `"2.5"`; `int()` là chỗ duy nhất chặn
    một trần phân số đi vào phép so với số lượng vị thế, và cùng giá trị đó đi
    tiếp vào phép nhân ngân sách rủi ro.
    """
    assert _with_config("champion", 2.9, risk.max_concurrent_positions) == 2
    assert isinstance(_with_config("champion", 2.9, risk.max_concurrent_positions), int)


def main():
    tests = [
        test_the_default_profile_uses_the_configured_cap,
        test_the_sr_profile_is_pinned_to_one_slot,
        test_the_entry_gate_reads_the_cap_through_one_function,
        test_the_risk_budget_scales_with_the_same_cap_the_gate_counts,
        test_the_cap_is_a_whole_number_of_slots,
    ]
    for test in tests:
        test()
        print(f"ok   {test.__name__}")
    print(f"{len(tests)} passed")


if __name__ == "__main__":
    main()
