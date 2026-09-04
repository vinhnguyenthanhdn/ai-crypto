"""MAX_CROSS_EXCHANGE_DIVERGENCE_PCT: lớp cuối trong bảng § Stops chưa có cổng.

Nó khác năm lớp còn lại ở một điểm quyết định cách viết cổng: nó không đọc trạng
thái nội bộ (ledger, số lệnh mở, đồng hồ) mà đọc **thế giới bên ngoài**, rồi kết
luận rằng dữ liệu vào đang sai — không phải rằng thị trường đang xấu. Ba hệ quả:

1. `None` (không lấy được giá tham chiếu) **không** được là veto. Một sàn thứ hai
   không trả lời là chuyện thường xuyên; biến nó thành veto là tự tắt bot mỗi lần
   một endpoint chậm, và không lần chạy nào phân biệt được hai trạng thái đó.
2. Cổng mặc định **TẮT** (`CROSS_EXCHANGE_DIVERGENCE_GATE_ENABLED=false`), nên
   mọi lần chạy thật hôm nay đi qua nhánh không-veto. Đó là lý do một khiếm khuyết
   ở đây sống được rất lâu: đường sai không nằm trên đường đang chạy.
3. Luật phải sống ở một chỗ gọi được. Trước cổng này nó là một biểu thức viết
   ngay trong `run.py:_handle_entry`, tức chỉ chạm tới được bằng cách dựng cả
   vòng chính — sàn, state store, feature store — nên nó chưa từng bị chấm.

Giá trị của các ca biên chọn theo luật đã ghi ở `#44`: phải nằm **giữa** ngưỡng
thật và ngưỡng mà đột biến dời tới, chứ không chỉ ở phía đúng của ngưỡng thật.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config  # noqa: E402
from src.engine import risk  # noqa: E402

LIMIT = 0.15


def test_a_divergence_past_the_limit_vetoes():
    veto, reason = risk.basis_risk_veto(0.4, enabled=True, limit_pct=LIMIT)
    assert veto
    assert "0.4" in reason and str(LIMIT) in reason, (
        "lý do phải nêu cả mức lệch đo được và ngưỡng: người đọc log quyết định "
        f"chỉnh ngưỡng dựa vào khoảng cách giữa hai số, got [{reason}]"
    )


def test_the_reference_being_below_the_venue_vetoes_the_same():
    """Vế dấu, và nó là chỗ `abs()` biến mất mà mọi ca một chiều vẫn xanh.

    Giá tham chiếu thấp hơn sàn thực thi đúng bằng thế là cùng một lỗi data.
    `-0.4` nằm giữa hai ngưỡng mà đột biến dời tới: bỏ `abs()` thì phép so thành
    `-0.4 > 0.15`, sai, nên nửa số ca hỏng trở thành hợp lệ.
    """
    veto, reason = risk.basis_risk_veto(-0.4, enabled=True, limit_pct=LIMIT)
    assert veto, "lệch âm cũng là lỗi data"
    assert "-0.4" in reason


def test_the_limit_itself_is_not_a_fault():
    """Ngưỡng là mức chấp nhận được, không phải mức bị chặn — `>` chứ không `>=`.

    Đúng tại ngưỡng phải vào lệnh; ngay trên nó thì không. Không có cặp này thì
    một đột biến đổi chiều dấu so sánh vẫn xanh với mọi giá trị xa ngưỡng.
    """
    assert risk.basis_risk_veto(LIMIT, enabled=True, limit_pct=LIMIT) == (False, None)
    assert risk.basis_risk_veto(-LIMIT, enabled=True, limit_pct=LIMIT) == (False, None)
    assert risk.basis_risk_veto(LIMIT + 0.01, enabled=True, limit_pct=LIMIT)[0]


def test_both_sides_of_the_comparison_are_percentages():
    """Vế đơn vị. Cả `reference_diff_pct` và ngưỡng là phần trăm, và một phép
    chia/nhân 100 ở một đầu vẫn cho quyết định đúng ở phần lớn giá trị.

    `0.5` với ngưỡng `0.15` phải veto. Nếu mức lệch bị chia 100 thì `0.005` không
    veto; nếu ngưỡng bị nhân 100 thì `15` không veto. Một ca `40` sẽ sống qua đột
    biến thứ nhất, và một ca `0.001` sống qua đột biến thứ hai.
    """
    assert risk.basis_risk_veto(0.5, enabled=True, limit_pct=LIMIT)[0]
    assert risk.basis_risk_veto(0.001, enabled=True, limit_pct=LIMIT) == (False, None)


def test_a_missing_reference_price_is_not_a_veto():
    """Thiếu dữ liệu khác dữ liệu sai, và trộn hai cái là tự tắt bot.

    Sàn tham chiếu chỉ để đối chiếu và `run.py` đã ghi rõ lỗi/None không chặn
    pipeline chính. Một đột biến biến `None` thành veto sẽ chặn **mọi** lệnh mỗi
    khi endpoint thứ hai chậm, và log chỉ nói 'nghi ngờ lỗi data'.
    """
    assert risk.basis_risk_veto(None, enabled=True, limit_pct=LIMIT) == (False, None)


def test_the_switch_is_what_decides_whether_the_layer_exists():
    """Công tắc, chấm ở mức lệch mà nhánh bật chắc chắn veto.

    Mặc định TẮT nên đây là nhánh mọi lần chạy thật đang đi qua. Ca này là chỗ
    duy nhất phân biệt 'cổng tắt' với 'cổng bật và không thấy gì' — hai trạng
    thái cho cùng một output trong mọi log.
    """
    assert risk.basis_risk_veto(9.0, enabled=False, limit_pct=LIMIT) == (False, None)
    assert risk.basis_risk_veto(9.0, enabled=True, limit_pct=LIMIT)[0]


def test_the_defaults_come_from_config_not_from_the_call_site():
    """Không truyền gì thì cả công tắc và ngưỡng phải đọc `config`.

    Vế này là thứ giữ cho cổng nói về hệ thống thật: một hàm chỉ đúng khi được
    truyền tham số tường minh là một hàm chưa ai chấm ở cấu hình đang chạy.
    """
    old_on, old_limit = (config.CROSS_EXCHANGE_DIVERGENCE_GATE_ENABLED,
                         config.MAX_CROSS_EXCHANGE_DIVERGENCE_PCT)
    try:
        config.CROSS_EXCHANGE_DIVERGENCE_GATE_ENABLED = True
        config.MAX_CROSS_EXCHANGE_DIVERGENCE_PCT = 0.2
        assert risk.basis_risk_veto(0.25)[0], "ngưỡng phải đọc từ config"
        assert risk.basis_risk_veto(0.15) == (False, None)

        config.CROSS_EXCHANGE_DIVERGENCE_GATE_ENABLED = False
        assert risk.basis_risk_veto(9.0) == (False, None), "công tắc phải đọc từ config"
    finally:
        (config.CROSS_EXCHANGE_DIVERGENCE_GATE_ENABLED,
         config.MAX_CROSS_EXCHANGE_DIVERGENCE_PCT) = old_on, old_limit


def test_the_entry_path_applies_the_rule_through_one_function():
    """Cổng chống drift, hỏi tính chất thay vì liệt kê dòng.

    `run.py` là chỗ duy nhất *áp* lớp này. Nó không được đọc hằng số trực tiếp:
    một biểu thức viết tại chỗ là chỗ ba vế trên biến mất khỏi mọi phép đo, và
    đó đúng là trạng thái của lớp này trước hôm nay. Khẳng định rỗng-là-lỗi đi
    kèm — `run.py` thôi gọi hàm thì cổng phải đỏ, không thành no-op im lặng.
    """
    source = (Path(__file__).resolve().parents[1] / "src" / "run.py").read_text(encoding="utf-8")

    assert "config.MAX_CROSS_EXCHANGE_DIVERGENCE_PCT" not in source, (
        "run.py đọc ngưỡng trực tiếp — đó là một bản chép của cả ba vế"
    )
    assert "config.CROSS_EXCHANGE_DIVERGENCE_GATE_ENABLED" not in source, (
        "run.py đọc công tắc trực tiếp — công tắc và ngưỡng phải rời khỏi run.py cùng nhau"
    )
    assert "risk.basis_risk_veto(" in source, (
        "đường vào lệnh phải đi qua đúng một hàm; không gọi nữa nghĩa là lớp này biến mất"
    )


def _run():
    """Các ca được phát hiện theo tiền tố, không liệt kê tên.

    Một danh sách viết tay ở cuối file là cùng lớp lỗi với một bước CI gọi tên
    file: ca thêm sau không bao giờ chạy, và suite vẫn báo thành công. Kèm khẳng
    định rỗng-là-lỗi, vì phát hiện theo tính chất cũng hỏng im lặng được.
    """
    cases = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    if not cases:
        print("FAIL không phát hiện được ca nào — phép phát hiện đã hỏng")
        return 1

    failures = 0
    for name, fn in cases:
        try:
            fn()
            print(f"ok   {name}")
        except Exception as exc:  # một crash cũng là một ca đỏ, không phải một ca vắng
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(cases)} case(s), {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run())
