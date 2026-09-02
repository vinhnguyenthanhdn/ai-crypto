"""Cooldown: sau mỗi lần thoát lệnh, chặn entry đúng một cửa sổ rồi tự mở lại.

`docs/decisions.md:94` liệt kê Cooldown là một trong các gate của Risk Engine, và
`src/run.py:274` đọc `cooldown_remaining_seconds(config.COOLDOWN_MINUTES)` mỗi
vòng để quyết có được vào lệnh không — `_handle_entry` trả `IGNORE` kèm
`gate="cooldown"` khi nó lớn hơn 0. Trước file này, `COOLDOWN_MINUTES` xuất hiện
đúng 0 lần trong mọi `scripts/test_*.py`: cặp `record_exit_now` (ghi) và
`cooldown_remaining_seconds` (đọc) chưa từng gặp nhau trong một phép đo nào.

Lớp này **đối xứng và tự hết hạn**, ngược hẳn kill switch. Đó là toàn bộ lý do nó
tồn tại riêng: kill switch dừng vì một điều kiện không tự hết, còn cooldown dừng
vì thị trường vừa đổi trạng thái và mấy phút sau thì hết. Gộp hai lớp — hoặc để
một đột biến làm cooldown không tự hết — biến một lần thoát lệnh bình thường
thành khoá vĩnh viễn, và không có gì quan sát được báo: `run.py` chỉ in dòng
"đang trong cooldown" cho mỗi vòng, y hệt một cooldown khoẻ mạnh.

Đơn vị là chỗ thứ hai đáng chấm. Hàm nhận **phút** và trả **giây**, hai đơn vị
khác nhau đi qua một phép nhân duy nhất; một ca chỉ hỏi "còn lớn hơn 0 không" thì
`* 60` biến mất mà vẫn xanh, và cửa sổ 30 phút rút xuống 30 giây.

Ca cuối so hai cách viết cùng một thời điểm (`+00:00` và `+07:00`). Nó không phụ
thuộc múi giờ của máy chạy: hai chuỗi lệch nhau 7 giờ, nên bất kỳ đột biến nào
đọc timestamp mà bỏ offset đều làm hai vế lệch nhau đúng 7 giờ ở mọi nơi. Đó là
cách duy nhất chấm được vế "cùng một instant" mà không phải cố định TZ của runner.
"""
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config, state_store  # noqa: E402

_TMP_DIR = tempfile.mkdtemp(prefix="ai_crypto_test_cooldown_")
_counter = 0


def _fresh_db():
    """DB riêng cho từng ca; `last_exit_at` là một khoá kv nên dùng lại là rò."""
    global _counter
    _counter += 1
    config.DB_PATH = Path(_TMP_DIR) / f"cooldown_{_counter}.db"
    return config.DB_PATH


def _window_seconds() -> float:
    return float(config.COOLDOWN_MINUTES) * 60


def _remaining() -> float:
    """Đúng lời gọi của `run.py:274` — cấu hình do caller đưa vào, không đọc lén."""
    return state_store.cooldown_remaining_seconds(config.COOLDOWN_MINUTES)


def _exit_at(seconds_ago: float, tz=timezone.utc):
    ts = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    state_store.record_exit_now(ts=ts.astimezone(tz).isoformat())


def test_no_exit_recorded_leaves_entries_open():
    """Ca lành mạnh. Không có nó, một cooldown chặn-mọi-lúc vẫn thoả mọi ca khác."""
    _fresh_db()
    assert _remaining() == 0.0


def test_an_exit_blocks_for_the_configured_window_in_seconds():
    """Cửa sổ phải đúng độ dài đã cấu hình, không chỉ đúng dấu.

    Hàm nhận phút và trả giây. Một ca chỉ đòi `> 0` để `* 60` biến mất mà vẫn
    xanh, và cooldown 30 phút thành 30 giây — đủ ngắn để hệ vào lại lệnh ngay
    trong cùng nhịp giá vừa làm nó thoát, tức đúng thứ lớp này sinh ra để chặn.
    """
    _fresh_db()
    window = _window_seconds()
    _exit_at(0)

    remaining = _remaining()
    assert 0 < remaining <= window, remaining
    # Biên dưới rộng 30 giây để chịu được máy chậm, vẫn hẹp hơn nhiều lần khoảng
    # cách tới bất kỳ đơn vị nào khác (giây, giờ) mà một đột biến có thể chọn.
    assert remaining > window - 30, remaining


def test_a_partly_elapsed_window_reports_what_is_left():
    """Phần còn lại giảm theo thời gian đã trôi, không phải một hằng số."""
    _fresh_db()
    window = _window_seconds()
    elapsed = window / 2
    _exit_at(elapsed)

    remaining = _remaining()
    assert abs(remaining - (window - elapsed)) < 30, remaining


def test_the_window_expires_on_its_own():
    """Vế phân biệt lớp này với kill switch, và vế đắt nhất nếu hỏng.

    Kill switch cố tình không tự tắt vì điều kiện làm nó bật không tự hết.
    Cooldown thì ngược lại — nó là một quãng nghỉ, và một quãng nghỉ không tự
    hết là một lần dừng vĩnh viễn mà log đọc y hệt một cooldown bình thường.

    Đòi đúng `0.0` chứ không đòi `<= 0`: `max(0.0, remaining)` là chỗ duy nhất
    kẹp giá trị, và một cửa sổ đã qua từ lâu cho một số âm rất lớn nếu bỏ nó.
    Số âm đó lọt qua cổng của `run.py` (`> 0`) nhưng là một lời khai sai về
    "còn bao nhiêu giây", thứ mà mọi chỗ hiển thị hay chờ theo nó đều đọc.
    """
    _fresh_db()
    _exit_at(_window_seconds() + 60)

    assert _remaining() == 0.0


def test_a_later_exit_restarts_the_window():
    """Lần thoát mới đặt lại cửa sổ, không phải bị lần đầu tiên giữ chỗ.

    `record_exit_now` ghi đè khoá kv. Đổi nó thành ghi-nếu-chưa-có thì ca đầu
    tiên và ca hết hạn đều vẫn xanh, trong khi hệ chỉ còn nghỉ đúng một lần
    trong cả vòng đời cơ sở dữ liệu.
    """
    _fresh_db()
    window = _window_seconds()

    _exit_at(window + 60)
    assert _remaining() == 0.0, "cửa sổ cũ lẽ ra đã hết"

    _exit_at(0)
    remaining = _remaining()
    assert remaining > window - 30, remaining


def test_two_spellings_of_the_same_instant_agree():
    """Cùng một thời điểm viết bằng hai offset phải cho cùng phần còn lại.

    `datetime.fromisoformat(...).timestamp()` trên một timestamp **naive** đọc
    theo múi giờ của máy, nên một đột biến bỏ offset trước khi parse dời thời
    điểm thoát đi đúng bằng offset đó — cửa sổ dài thêm hoặc hết sớm hàng giờ,
    tuỳ máy. Phép so hai cách viết không phụ thuộc TZ của runner: hai chuỗi lệch
    nhau 7 giờ nên chúng không thể cùng sai một lượng ở bất kỳ nơi nào.
    """
    window = _window_seconds()
    elapsed = window / 2
    tz_utc7 = timezone(timedelta(hours=7))

    _fresh_db()
    _exit_at(elapsed, tz=timezone.utc)
    as_utc = _remaining()

    _fresh_db()
    _exit_at(elapsed, tz=tz_utc7)
    as_utc7 = _remaining()

    assert abs(as_utc - as_utc7) < 30, (as_utc, as_utc7)
    assert abs(as_utc - (window - elapsed)) < 30, as_utc


def main():
    tests = [
        test_no_exit_recorded_leaves_entries_open,
        test_an_exit_blocks_for_the_configured_window_in_seconds,
        test_a_partly_elapsed_window_reports_what_is_left,
        test_the_window_expires_on_its_own,
        test_a_later_exit_restarts_the_window,
        test_two_spellings_of_the_same_instant_agree,
    ]
    original_db = config.DB_PATH
    try:
        for test in tests:
            test()
            print(f"ok   {test.__name__}")
    finally:
        config.DB_PATH = original_db
    print(f"{len(tests)} passed")


if __name__ == "__main__":
    main()
