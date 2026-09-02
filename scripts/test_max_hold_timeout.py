"""MAX_HOLD_MINUTES: lớp bảo vệ chặn một vị thế tồn tại vô hạn.

`src/config.py:133` nói thẳng lý do lớp này tồn tại — "vị thế không được tồn tại
vô hạn; quá horizon này phải đóng bằng TIMEOUT_EXIT ở live/backtest" — và cả hai
đường thoát mà `src/run.py:_handle_exit` gọi đều đi qua `_max_hold_reached`:
`decide_exit` cho profile thường và `decide_support_resistance_exit` cho profile
`support_resistance_only`.

**Chỗ này khác bốn lớp bảo vệ trước: nó không trống hẳn.** `test_sr_scoring.py:
test_horizon_no_min_and_timeout` đã có một ca cho `decide_exit`, và ca đó đủ mạnh
để giết ba đột biến (xoá hẳn nhánh timeout khỏi `decide_exit`; `>=` thành `>`;
nhánh timeout xuống dưới MACD/RSI). Đo trước khi viết file này chứ không suy —
nếu không thì mọi ca ở đây chỉ là bản chép thứ hai của một phép đo đã có.

Năm thứ ca đó **không** chấm, và cả năm đều để đột biến sống:

1. **Đơn vị.** `holding_minutes` chia 60 đúng một lần. Ca cũ giữ lệnh đúng
   `1440` phút, và `1440` giây cũng `>= 1440`, nên bỏ phép chia vẫn xanh — trong
   khi mọi vị thế quá 24 *giây* bị đóng ngay.
2. **Đường SR.** `decide_support_resistance_exit` là đường thoát live của profile
   `support_resistance_only` và không ca nào chạm; nó cũng là đường duy nhất
   **không nhận** `max_hold_minutes`, tức luôn đọc config.
3. **Công tắc tắt.** `limit > 0` là chỗ duy nhất tắt được lớp này. Bỏ nó thì
   `max_hold_minutes=0` biến thành "đóng mọi vị thế ngay lập tức", đảo ngược ý
   nghĩa của giá trị.
4. **Múi giờ.** `entry_time` đến từ DB dưới dạng chuỗi ISO. Một đột biến bỏ
   offset trước khi parse dời thời điểm vào lệnh đi đúng bằng offset đó.
5. **Thứ tự nhánh.** Timeout đứng sau stop/TP ở cả hai đường; đưa nó lên trước
   thì mọi lần chạm stop của một vị thế cũ vào sổ dưới nhãn TIMEOUT_EXIT.

Kỳ vọng ở đây suy từ `config.MAX_HOLD_MINUTES` chứ không viết lại số `1440`: ca
cũ chép hằng số, nên đổi horizon trong config làm nó chấm một con số không còn
ai dùng mà vẫn xanh.

**Ca cố tình không viết, vì nó không thể đỏ:** "`entry_time` vắng thì không bao
giờ timeout". Nghe như đúng thứ đáng ghim, nhưng nhánh đó được **ba** lớp độc
lập chặn — `_max_hold_reached` trả `False` ngay, `holding_minutes` trả `0.0`, và
`max(0.0, …)` kẹp phần còn lại — nên không đột biến nào của lớp timeout, kể cả
đột biến gộp hai lớp cùng lúc (đo thật: vẫn xanh), làm nó quan sát được. Một
assertion không có khả năng đỏ chỉ thêm một dòng xanh vĩnh viễn vào báo cáo.
Hai lớp trong ba là dư, và chúng dư một cách không đo được — cùng họ với hàm
public 0 caller ở `#47`, khác ở chỗ guard này không mang luật nào nên để lại
không tốn gì ngoài chỗ đọc.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config  # noqa: E402
from src.engine import decision  # noqa: E402

_ENTRY = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _limit() -> float:
    return float(config.MAX_HOLD_MINUTES)


def _neutral_frame() -> pd.DataFrame:
    """Khung không kích hoạt bất kỳ điều kiện thoát nào ngoài timeout.

    MACD phẳng (không có cắt xuống), RSI giữa dải, và `volume == vol_sma20` nên
    nhánh volume-yếu không mở. Không có khung này thì một ca timeout không phân
    biệt được với một lần thoát vì lý do khác.
    """
    row = {"open": 100, "close": 100, "volume": 100, "vol_sma20": 100,
           "macd": 0, "macd_signal": 0, "rsi": 50, "ema20": 101, "ema50": 100}
    return pd.DataFrame([dict(row), dict(row)])


def _position(entry_time=_ENTRY) -> dict:
    """Stop/TP đặt xa giá xét để hai nhánh đứng trước timeout không chen vào."""
    return {"entry_time": entry_time, "stop_price": 50, "take_profit_price": 200}


def _long_exit(position, minutes_held, **kwargs):
    return decision.decide_exit(
        position, 100, _neutral_frame(),
        current_time=_ENTRY + timedelta(minutes=minutes_held), **kwargs,
    )


def _sr_exit(position, minutes_held, sell_score=0.0):
    return decision.decide_support_resistance_exit(
        position, 100, sell_score,
        current_time=_ENTRY + timedelta(minutes=minutes_held),
    )


def test_a_young_position_is_not_closed_at_all():
    """Ca lành mạnh, và nó là ca giết đột biến đơn vị.

    Giữ lệnh nửa horizon: tính bằng phút thì còn xa ngưỡng, tính bằng giây thì
    đã vượt gấp nhiều lần. Không có ca này, một lớp timeout đóng-mọi-lúc vẫn
    thoả mọi ca khác trong file — kể cả ca biên.
    """
    exited, reason = _long_exit(_position(), _limit() / 2)
    assert not exited, reason
    assert reason == "", reason


def test_the_horizon_is_the_configured_one_and_the_boundary_closes():
    """Ngưỡng phải là giá trị đang cấu hình, không phải một hằng số chép tay.

    Hai vế cách nhau một phút quanh biên: đúng horizon thì đóng, thiếu một phút
    thì không. Vế thứ hai là vế phân biệt `>=` với `>`, và nó chỉ có nghĩa khi
    biên lấy từ `config` — ca chép số sẽ chấm đúng ở một horizon mà không còn
    module nào đọc.
    """
    limit = _limit()

    exited, reason = _long_exit(_position(), limit)
    assert exited and reason.startswith("TIMEOUT_EXIT"), reason
    assert f"{limit:g}" in reason, reason

    exited, reason = _long_exit(_position(), limit - 1)
    assert not exited, reason


def test_both_live_exit_paths_share_the_same_horizon():
    """`_handle_exit` rẽ hai nhánh theo `scoring_profile`; cả hai phải cùng đóng.

    Đường `support_resistance_only` chưa từng có ca nào cho lớp này, và nó là
    đường duy nhất **không nhận** `max_hold_minutes` — nó luôn đọc config, nên
    một caller đặt horizon riêng cho profile thường sẽ lệch âm thầm khỏi profile
    SR. Ca này chấm điều duy nhất còn đúng cho cả hai: cùng một horizon.
    """
    limit = _limit()

    for minutes, expect_exit in ((limit, True), (limit / 2, False)):
        long_exited, long_reason = _long_exit(_position(), minutes)
        sr_exited, sr_reason = _sr_exit(_position(), minutes)

        assert long_exited is expect_exit, (minutes, long_reason)
        assert sr_exited is expect_exit, (minutes, sr_reason)
        if expect_exit:
            assert long_reason.startswith("TIMEOUT_EXIT"), long_reason
            assert sr_reason.startswith("TIMEOUT_EXIT"), sr_reason


def test_zero_turns_the_horizon_off_instead_of_closing_everything():
    """`limit > 0` là công tắc tắt; bỏ nó thì `0` nghĩa là ngược lại hẳn.

    Không có ca này, một đột biến xoá vế `limit > 0` giữ mọi ca khác xanh (mọi
    ca khác dùng horizon dương), trong khi một lần cấu hình `MAX_HOLD_MINUTES=0`
    — cách duy nhất tắt lớp này — đóng mọi vị thế ở vòng đầu tiên.
    """
    exited, reason = _long_exit(_position(), _limit() * 10, max_hold_minutes=0)
    assert not exited, reason


def test_two_spellings_of_the_same_instant_agree():
    """Cùng một thời điểm vào lệnh viết bằng hai offset phải cho cùng phán quyết.

    `entry_time` đi qua DB dưới dạng chuỗi ISO. Một đột biến bỏ offset trước khi
    parse dời thời điểm vào lệnh đúng bằng offset đó, và phép so này không phụ
    thuộc TZ của runner: hai chuỗi lệch nhau 7 giờ nên chúng không thể cùng sai
    một lượng ở bất kỳ nơi nào.

    Thời điểm xét chọn cách biên **2 giờ về phía đã quá hạn**, tức trong khoảng
    mà 7 giờ lệch đủ để lật phán quyết — cách biên xa hơn 7 giờ thì hai vế cùng
    đóng hoặc cùng mở và đột biến sống.
    """
    limit = _limit()
    held = limit + 120
    instant = _ENTRY + timedelta(minutes=0)
    tz_utc7 = timezone(timedelta(hours=7))

    as_utc = _long_exit(_position(entry_time=instant.isoformat()), held)
    as_utc7 = _long_exit(
        _position(entry_time=instant.astimezone(tz_utc7).isoformat()), held,
    )

    assert as_utc[0] is as_utc7[0], (as_utc, as_utc7)
    assert as_utc[0] and as_utc[1].startswith("TIMEOUT_EXIT"), as_utc


def test_an_aged_position_that_hit_its_stop_still_reports_the_stop():
    """Thứ tự nhánh là một lời khai: lý do thoát đi vào sổ và vào accounting.

    Stop/TP đứng trước timeout trong cả hai đường. Đưa timeout lên trước làm mọi
    lần chạm stop của một vị thế cũ được ghi là TIMEOUT_EXIT, và bảng lý do thoát
    thôi phân biệt được "hết horizon" với "lỗ tới stop".
    """
    aged = _limit() + 60

    exited, reason = decision.decide_exit(
        _position(), 40, _neutral_frame(),
        current_time=_ENTRY + timedelta(minutes=aged),
    )
    assert exited and "stop loss" in reason, reason

    exited, reason = decision.decide_support_resistance_exit(
        _position(), 40, 0.0,
        current_time=_ENTRY + timedelta(minutes=aged),
    )
    assert exited and reason.startswith("STOP_LOSS"), reason


def main():
    tests = [
        test_a_young_position_is_not_closed_at_all,
        test_the_horizon_is_the_configured_one_and_the_boundary_closes,
        test_both_live_exit_paths_share_the_same_horizon,
        test_zero_turns_the_horizon_off_instead_of_closing_everything,
        test_two_spellings_of_the_same_instant_agree,
        test_an_aged_position_that_hit_its_stop_still_reports_the_stop,
    ]
    for test in tests:
        test()
        print(f"ok   {test.__name__}")
    print(f"{len(tests)} passed")


if __name__ == "__main__":
    main()
