"""README § Stops khai gate nào đã có test và gate nào chưa — cổng này chấm lời khai đó.

Bảng trong README là chỗ người đọc tra "vì sao nó không vào lệnh", và ngay dưới
bảng có một câu nói thẳng gate nào **chưa** được test bao. Lời khai đó đúng vào
ngày viết rồi lệch dần: mỗi lần một gate có test mới, câu kia vẫn nằm nguyên và
không gì báo. Nó là danh sách chép tay thứ hai sống cạnh mã, cùng lớp với
`required_status_checks.contexts` và với `files` trong `package.json` — và lệch
theo cùng một hướng, vì thứ **mới nhất** là thứ không ai quay lại sửa.

Đắt hơn một lệch tài liệu bình thường: câu đó là một lời thú nhận, nên nó chỉ
sai được về phía **khai phủ nhiều hơn thực tế**. Một người đọc repo để đánh giá
sẽ tin đúng câu này, vì tự nhận chưa phủ là thứ không ai bịa.

Cổng hỏi tính chất, không giữ bản chép thứ ba: tên gate rút từ chính bảng, tập
"chưa phủ" rút từ chính câu đó, và "đã phủ" đo bằng việc tên hằng số có xuất hiện
trong một `scripts/test_*.py` nào không. Hai tập phải bù nhau đúng bằng bảng.

Ba vế bắt buộc, thiếu vế nào thì cổng xanh rỗng:

1. **Rỗng là lỗi.** Phép rút bảng thôi khớp — đổi cấu trúc bảng, đổi dấu backtick
   — thì tập gate rỗng, và rỗng bù rỗng là một đẳng thức luôn đúng.
2. **Cả hai chiều.** Khai chưa phủ trong khi đã có test cũng là lệch, không chỉ
   chiều ngược lại; nếu không thì một gate mới có cổng sẽ nằm mãi trong danh sách
   thú nhận và cổng vẫn xanh.
3. **Tên file trong câu khai phải tồn tại** và phải nằm trong đúng glob mà
   `scripts/run_suite.sh` chạy — một file được nêu mà suite không chạy là một
   lời khai về thứ không ai thực thi.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

README = ROOT / "README.md"
SUITE = sorted(ROOT.glob("scripts/test_*.py"))

_CONTROL = r"[A-Z][A-Z0-9_]{4,}"


def _readme() -> str:
    return README.read_text(encoding="utf-8")


def _table_rows() -> list[str]:
    """Thân bảng gate, lấy nguyên dòng: block liền mạch ngay sau dòng gạch ngang."""
    block = re.search(r"^\| Control \|[^\n]*\n\|[-| ]+\|\n((?:\|[^\n]*\n)+)", _readme(), re.M)
    assert block, "README không còn bảng gate mở đầu bằng cột Control"
    return block.group(1).strip().splitlines()


def _table_controls() -> list[str]:
    """Cột đầu của bảng gate.

    Đòi **mọi** dòng thân bảng cho ra một tên, không chỉ những dòng còn khớp:
    một dòng thôi khớp thì gate của nó lặng lẽ rơi khỏi tập được chấm, và cổng
    vẫn xanh vì các dòng còn lại vẫn cân. Đây là ca duy nhất sống sót ở vòng đo
    đầu tiên, và nó sống đúng vì phép rút chỉ nói được về thứ nó rút ra được.
    """
    controls = []
    for row in _table_rows():
        name = re.match(rf"\| `({_CONTROL})` \|", row)
        assert name, f"dòng bảng không cho ra tên gate: {row}"
        controls.append(name.group(1))
    return controls


_FULL_COVERAGE = "Every gate in the table now has one."


def _coverage_claim() -> tuple[str, set[str]]:
    """Lời khai của README về phạm vi phủ, ở đúng một trong hai hình dạng.

    Bản đầu của cổng này đòi câu tự nhận "chưa phủ" **luôn tồn tại**, và nó chết
    ngay ngày gate cuối cùng có test — một cổng chỉ đúng khi lời thú nhận còn dở
    dang là một cổng không sống qua chính thành công nó đo. Trạng thái phủ đủ vẫn
    là một lời khai, và nó vẫn sai được: gate thêm vào bảng sau đó không có test
    thì câu này thành nói quá, nên nó phải được rút ra và chấm như câu kia.
    """
    partial = re.search(r"([^.]*\bnot\s+covered yet[^.]*\.)", _readme(), re.S)
    full = re.search(_FULL_COVERAGE.replace(" ", r"\s+"), _readme()) is not None
    assert partial or full, (
        "README không khai phạm vi phủ theo hình dạng nào: cần một câu "
        f"'not covered yet' hoặc đúng câu '{_FULL_COVERAGE}'"
    )
    assert not (partial and full), (
        "README khai cả 'chưa phủ' và phủ đủ cùng lúc — hai lời khai ngược nhau"
    )
    if partial:
        return "partial", set(re.findall(rf"`({_CONTROL})`", partial.group(1)))
    return "full", set()


def _declared_uncovered() -> set[str]:
    """Tên hằng số README tự nhận chưa có test; rỗng khi nó khai phủ đủ."""
    return _coverage_claim()[1]


def _declared_test_files() -> set[str]:
    sentence = re.search(r"Gates with a test under `scripts/`:(.+?\.)\s", _readme(), re.S)
    assert sentence, "README không còn câu liệt kê test của các gate"
    return set(re.findall(r"`(test_[a-z0-9_]+\.py)`", sentence.group(1)))


def _tests_naming(control: str) -> list[str]:
    return [p.name for p in SUITE
            if p.name != Path(__file__).name and control in p.read_text(encoding="utf-8")]


def test_the_table_is_readable():
    controls = _table_controls()
    assert len(controls) >= 4, controls
    assert len(set(controls)) == len(controls), controls
    assert SUITE, "glob scripts/test_*.py rỗng"


def test_every_gate_is_either_tested_or_declared_untested():
    """Đo từng gate hai chiều, và báo cả hai loại lệch trong một lần chạy."""
    uncovered = _declared_uncovered()
    wrong = []
    for control in _table_controls():
        naming = _tests_naming(control)
        declared = control in uncovered
        if naming and declared:
            wrong.append(f"{control}: README khai chưa phủ nhưng {naming} đã nêu nó")
        elif not naming and not declared:
            wrong.append(f"{control}: không test nào nêu nó, README cũng không khai là chưa phủ")
    assert not wrong, "\n".join(wrong)
    assert uncovered <= set(_table_controls()), uncovered


def test_the_coverage_claim_has_exactly_one_shape():
    """Hai hình dạng lời khai là loại trừ nhau, và vắng cả hai cũng là lệch.

    Không có ca này thì một lần sửa README bỏ cả hai câu biến cổng thành no-op:
    tập "chưa phủ" rỗng bù đúng bằng tập gate đã có test, và mọi thứ vẫn cân.
    """
    mode, uncovered = _coverage_claim()
    assert mode in ("partial", "full")
    if mode == "full":
        assert not uncovered
        for control in _table_controls():
            assert _tests_naming(control), (
                f"README khai mọi gate đã có test, nhưng không test nào nêu {control}"
            )


def test_the_named_test_files_exist_and_run():
    """Suite chạy bằng glob, nên tồn tại trên đĩa là đủ điều kiện được chạy —
    vế cần chấm là tên trong README có trỏ tới file thật hay không."""
    named = _declared_test_files()
    assert named, "câu liệt kê không rút được tên file nào"
    on_disk = {p.name for p in SUITE}
    assert named <= on_disk, named - on_disk


def main():
    tests = [
        test_the_table_is_readable,
        test_the_coverage_claim_has_exactly_one_shape,
        test_every_gate_is_either_tested_or_declared_untested,
        test_the_named_test_files_exist_and_run,
    ]
    for test in tests:
        test()
        print(f"ok   {test.__name__}")
    print(f"{len(tests)} passed")


if __name__ == "__main__":
    main()
