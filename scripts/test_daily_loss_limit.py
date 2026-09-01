"""Daily-loss limit: chốt trong ngày khi lỗ thực hiện chạm ngưỡng, mở lại sang ngày mới.

README § Configuration khai `DAILY_LOSS_LIMIT_PCT` là "daily realized-loss limit,
tracked in `daily_pnl`", và `run.py` đọc `is_trading_halted_today()` mỗi vòng để
quyết có được vào lệnh không. Trước file này, tên đó xuất hiện đúng 0 lần trong
mọi `scripts/test_*.py`: cả cột `trading_halted` lẫn phép so ngưỡng sinh ra nó
chưa từng được chấm.

Lớp này bất đối xứng **trong phạm vi một ngày** và đối xứng **giữa các ngày**, và
hai vế đó hỏng theo hai hướng ngược nhau. Vế trong ngày: một lệnh thắng sau khi
đã chốt không được mở lại — nếu mở, hệ trade tiếp đúng ngày mà nó vừa quyết dừng,
và không có gì quan sát được báo. Vế giữa các ngày: chốt phải hết hạn lúc sang
ngày UTC mới — nếu không, một ngày xấu khoá hệ vĩnh viễn trong khi README gọi nó
là giới hạn *hằng ngày*.

Cách dựng ca theo đúng bài học của cổng kill switch: fixture nào cũng dựng bằng
cách **vi phạm** thì nhánh "tiền đề không còn đúng" không tồn tại để quan sát.
Ở đây tiền đề tụt lại được — lỗ thực hiện trong ngày là một tổng cộng dồn, cộng
thêm một lệnh thắng là nó về trên ngưỡng — nên ca số 3 dựng được thật, khác với
drawdown (mức đỉnh-đáy lịch sử, không tụt lại).

Số vào là PnL bằng USD, không phải phần trăm: phần trăm là thứ mã có nhiệm vụ tự
tính từ `start_equity_usd`, và một test truyền sẵn nó thì không còn đo gì.
"""
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config, state_store  # noqa: E402

_TMP_DIR = tempfile.mkdtemp(prefix="ai_crypto_test_daily_loss_")
_counter = 0


def _fresh_db():
    """DB riêng cho từng ca; `daily_pnl` cộng dồn theo ngày nên dùng lại là rò."""
    global _counter
    _counter += 1
    config.DB_PATH = Path(_TMP_DIR) / f"daily_loss_{_counter}.db"
    return config.DB_PATH


def _at(day: str, minute: int) -> str:
    return f"{day}T00:{minute:02d}:00+00:00"


def _usd(pct: float) -> float:
    """PnL bằng USD ứng với `pct`% của vốn mở đầu ngày."""
    return float(config.ACCOUNT_EQUITY_USD) * pct / 100


def _trade(trade_id: str, pnl_usd: float, ts: str) -> dict:
    return state_store.record_trade_accounting(
        trade_id, {"net_pnl_usd": pnl_usd}, ts=ts
    )


def _halted_flag(day: str):
    """Đọc thẳng cột mà mã ghi, để phân biệt 'chưa có dòng' với 'có và bằng 0'."""
    with state_store.get_conn() as conn:
        row = conn.execute(
            "SELECT trading_halted FROM daily_pnl WHERE trade_date = ?", (day,)
        ).fetchone()
    return None if row is None else int(row[0])


def test_a_loss_under_the_limit_leaves_trading_open():
    """Ca lành mạnh. Không có nó, một cổng chốt-với-mọi-thứ vẫn thoả mọi ca khác."""
    _fresh_db()
    day = "2026-03-02"
    _trade("under-1", -_usd(config.DAILY_LOSS_LIMIT_PCT / 2), _at(day, 1))

    assert _halted_flag(day) == 0
    assert state_store.is_trading_halted_at(_at(day, 5)) is False


def test_a_loss_at_the_limit_halts_trading():
    """Biên: `<=`, không phải `<`. Đúng ngưỡng là đã chạm giới hạn."""
    _fresh_db()
    day = "2026-03-03"
    _trade("at-1", -_usd(config.DAILY_LOSS_LIMIT_PCT), _at(day, 1))

    assert _halted_flag(day) == 1
    assert state_store.is_trading_halted_at(_at(day, 5)) is True


def test_the_limit_is_a_magnitude_not_a_signed_number():
    """`DAILY_LOSS_LIMIT_PCT` viết bằng số âm phải chốt ở cùng chỗ, không bao giờ chốt.

    Cấu hình đến từ biến môi trường và người vận hành đọc "loss limit" thành một
    số âm là cách hiểu hợp lý. Bỏ `abs()` thì bản khai `-5` cho ngưỡng `+5%`, và
    `daily_pct <= 5` đúng với gần như mọi ngày — hệ chốt ngay sau lệnh đầu tiên
    dù lãi hay lỗ. Cấu hình mặc định không quan sát được điều đó.

    Ca này chỉ đo được ở **một lệnh thắng nhỏ**: bên trong ngưỡng nếu đọc dấu sai,
    ngoài ngưỡng nếu đọc đúng. Bản đầu dùng một lệnh thắng lớn hơn ngưỡng và nó
    sống qua đúng đột biến nó sinh ra để chặn — hai phía cùng cho "không chốt".
    """
    _fresh_db()
    day = "2026-03-04"
    original = config.DAILY_LOSS_LIMIT_PCT
    try:
        config.DAILY_LOSS_LIMIT_PCT = -abs(original)
        _trade("mag-win", _usd(abs(original) / 2), _at(day, 1))
        assert _halted_flag(day) == 0, "một lệnh thắng vừa chốt hệ lại"

        _trade("mag-loss", -_usd(abs(original) * 3), _at(day, 2))
        assert _halted_flag(day) == 1
    finally:
        config.DAILY_LOSS_LIMIT_PCT = original


def test_a_winning_trade_after_the_halt_does_not_resume_trading():
    """Vế đắt nhất: chốt rồi thì trong ngày đó không có đường tự mở lại.

    Đây là ca mà tiền đề chốt **không còn đúng** — tổng lỗ trong ngày đã về trên
    ngưỡng — mà trạng thái vẫn phải giữ nguyên. `MAX(trading_halted, ?)` là chỗ
    duy nhất giữ nó; đổi thành `?` thì mọi ca khác vẫn xanh.
    """
    _fresh_db()
    day = "2026-03-05"
    limit = config.DAILY_LOSS_LIMIT_PCT

    _trade("halt-1", -_usd(limit * 1.5), _at(day, 1))
    assert _halted_flag(day) == 1

    _trade("halt-2", _usd(limit * 1.4), _at(day, 2))

    with state_store.get_conn() as conn:
        pct = conn.execute(
            "SELECT realized_pnl_pct FROM daily_pnl WHERE trade_date = ?", (day,)
        ).fetchone()[0]
    # Tiền đề đã tụt lại thật — nếu không, ca này không đo được cái nó khai.
    assert -limit < pct < 0, pct
    assert _halted_flag(day) == 1, "lệnh thắng vừa mở lại một ngày đã chốt"
    assert state_store.is_trading_halted_at(_at(day, 9)) is True


def test_the_halt_does_not_carry_into_the_next_utc_day():
    """Giới hạn *hằng ngày*: sang ngày UTC mới là mở, kể cả khi vốn chưa hồi.

    Vế ngược của ca trên, và nó phân biệt lớp này với Kill Switch — thứ cố tình
    không tự tắt. Gộp hai lớp làm một là biến một ngày xấu thành khoá vĩnh viễn.
    """
    _fresh_db()
    day = "2026-03-06"
    nxt = "2026-03-07"

    _trade("carry-1", -_usd(config.DAILY_LOSS_LIMIT_PCT * 2), _at(day, 1))
    assert state_store.is_trading_halted_at(_at(day, 9)) is True

    assert _halted_flag(nxt) is None
    assert state_store.is_trading_halted_at(_at(nxt, 1)) is False, (
        "chốt của hôm qua đang chặn hôm nay"
    )

    # Và ngày mới tự chấm lấy: một lệnh nhỏ không kéo theo lỗ hôm trước.
    _trade("carry-2", -_usd(config.DAILY_LOSS_LIMIT_PCT / 2), _at(nxt, 2))
    assert _halted_flag(nxt) == 0
    assert _halted_flag(day) == 1, "ngày cũ bị ghi đè"


def test_is_trading_halted_today_reads_the_current_utc_day():
    """Đường mà `run.py:553` thật sự đi, và nó hỏi đồng hồ chứ không nhận tham số.

    Ba ca trên dùng `is_trading_halted_at` để điều khiển được ngày; ca này chấm
    chính hàm vòng chạy gọi, nên một lỗi chọn sai ngày ở đó không đi qua được.
    """
    _fresh_db()
    now = datetime.now(timezone.utc)
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    # Hôm qua chốt, hôm nay chưa có lệnh nào.
    _trade("today-1", -_usd(config.DAILY_LOSS_LIMIT_PCT * 2), _at(yesterday, 1))
    assert state_store.is_trading_halted_today() is False

    _trade("today-2", -_usd(config.DAILY_LOSS_LIMIT_PCT * 2), now.isoformat())
    assert state_store.is_trading_halted_today() is True


def main():
    tests = [
        test_a_loss_under_the_limit_leaves_trading_open,
        test_a_loss_at_the_limit_halts_trading,
        test_the_limit_is_a_magnitude_not_a_signed_number,
        test_a_winning_trade_after_the_halt_does_not_resume_trading,
        test_the_halt_does_not_carry_into_the_next_utc_day,
        test_is_trading_halted_today_reads_the_current_utc_day,
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
