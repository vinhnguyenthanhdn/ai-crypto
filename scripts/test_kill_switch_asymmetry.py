"""Kill Switch bất đối xứng: hệ tự bật khi Max Drawdown vượt ngưỡng, không tự tắt.

README § Risk Engine khai đúng hai vế đó bằng chữ, và trước file này không phép
đo nào chạm vào chúng — `kill_switch`, `MAX_DRAWDOWN_PCT`, `DAILY_LOSS_LIMIT_PCT`
và `COOLDOWN_MINUTES` xuất hiện 0 lần trong toàn bộ `scripts/test_*.py`. Vế thứ
hai là vế đắt: một lỗi làm hệ tự tắt Kill Switch không hỏng ở đâu quan sát được,
nó chỉ lặng lẽ cho phép trade tiếp sau đúng lần sụt vốn mà nó sinh ra để chặn.

Drawdown đọc từ `equity_ledger` thật, không phải từ một tham số truyền vào, nên
test dựng ledger rồi để mã tự tính — tham số nào cung cấp sẵn con số mã có nhiệm
vụ tính ra thì nó không còn đo cái gì.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config, state_store  # noqa: E402

_TMP_DIR = tempfile.mkdtemp(prefix="ai_crypto_test_kill_switch_")
_counter = 0


def _fresh_db():
    """DB riêng cho từng ca; kill switch sống trong `kv_store` nên dùng lại là rò."""
    global _counter
    _counter += 1
    config.DB_PATH = Path(_TMP_DIR) / f"kill_switch_{_counter}.db"
    return config.DB_PATH


def _seed_equity(*values):
    """Ghi equity ledger; drawdown tính từ đỉnh, mốc đầu là ACCOUNT_EQUITY_USD."""
    with state_store.get_conn() as conn:
        offset = conn.execute("SELECT COUNT(*) FROM equity_ledger").fetchone()[0]
        for index, equity in enumerate(values, start=offset):
            conn.execute(
                "INSERT INTO equity_ledger"
                " (ts, trade_id, realized_pnl_usd, equity_before_usd, equity_after_usd,"
                "  return_on_equity_pct, accounting)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    f"2026-01-01T00:{index:02d}:00Z",
                    f"test-{index}",
                    0.0,
                    float(equity),
                    float(equity),
                    0.0,
                    "TEST",
                ),
            )


def test_drawdown_below_threshold_leaves_the_switch_off():
    _fresh_db()
    equity = float(config.ACCOUNT_EQUITY_USD)
    # Sụt nửa ngưỡng: dưới mức phải bật, và vẫn đủ lớn để không phải là 0.
    _seed_equity(equity, equity * (1 - config.MAX_DRAWDOWN_PCT / 200))

    measured = state_store.get_max_drawdown_pct()
    assert 0 < measured < config.MAX_DRAWDOWN_PCT, measured
    assert state_store.arm_kill_switch_if_drawdown_breached() is False
    assert state_store.is_kill_switch_on() is False
    assert state_store.get_kill_switch_reason() == ""


def test_drawdown_at_threshold_arms_the_switch_and_names_the_numbers():
    _fresh_db()
    equity = float(config.ACCOUNT_EQUITY_USD)
    _seed_equity(equity, equity * (1 - config.MAX_DRAWDOWN_PCT / 100))

    measured = state_store.get_max_drawdown_pct()
    assert measured >= config.MAX_DRAWDOWN_PCT, measured
    assert state_store.arm_kill_switch_if_drawdown_breached() is True
    assert state_store.is_kill_switch_on() is True

    reason = state_store.get_kill_switch_reason()
    # Lý do phải mang cả số đo được và ngưỡng nó vượt: người tắt tay đọc đúng
    # dòng này để quyết định, và một chuỗi chung không nói được đã sụt bao nhiêu.
    assert str(measured) in reason, reason
    assert str(config.MAX_DRAWDOWN_PCT) in reason, reason


def test_a_recovered_account_does_not_disarm_the_switch():
    _fresh_db()
    equity = float(config.ACCOUNT_EQUITY_USD)
    _seed_equity(equity, equity * (1 - config.MAX_DRAWDOWN_PCT / 100))
    assert state_store.arm_kill_switch_if_drawdown_breached() is True
    armed_reason = state_store.get_kill_switch_reason()

    # Vốn hồi lên đỉnh mới. Drawdown lịch sử không xoá được, nhưng kể cả nếu nó
    # xoá được thì luật vẫn không được phép tự tắt: chỉ người tắt.
    _seed_equity(equity * 2)
    assert state_store.arm_kill_switch_if_drawdown_breached() is False
    assert state_store.is_kill_switch_on() is True
    assert state_store.get_kill_switch_reason() == armed_reason


def test_a_healthy_ledger_does_not_disarm_a_switch_someone_else_armed():
    """Ca duy nhất chấm được vế 'không tự tắt' khi drawdown đang **dưới** ngưỡng.

    Ca hồi vốn ở trên không đủ: drawdown là mức đỉnh-đáy lịch sử nên nó không
    bao giờ tụt xuống dưới ngưỡng một lần đã vượt, tức nhánh 'lành mạnh' của
    luật không tồn tại trong nền của ca đó. Đo bằng đột biến: thêm một nhánh
    tự tắt khi `max_dd < MAX_DRAWDOWN_PCT` thì cả bốn ca kia vẫn xanh.

    Nền đúng có thật trong vận hành: Kill Switch còn được bật từ đường khác
    (người bật tay, hoặc một luật rủi ro khác), và lúc đó ledger đang lành.
    """
    _fresh_db()
    equity = float(config.ACCOUNT_EQUITY_USD)
    _seed_equity(equity, equity * (1 - config.MAX_DRAWDOWN_PCT / 200))
    assert state_store.get_max_drawdown_pct() < config.MAX_DRAWDOWN_PCT

    state_store.set_kill_switch(True, reason="armed by hand")
    assert state_store.arm_kill_switch_if_drawdown_breached() is False
    assert state_store.is_kill_switch_on() is True
    assert state_store.get_kill_switch_reason() == "armed by hand"


def test_only_an_explicit_call_turns_the_switch_off():
    _fresh_db()
    equity = float(config.ACCOUNT_EQUITY_USD)
    _seed_equity(equity, equity * (1 - config.MAX_DRAWDOWN_PCT / 100))
    state_store.arm_kill_switch_if_drawdown_breached()
    assert state_store.is_kill_switch_on() is True

    # Đúng đường mà `scripts/kill_switch.py off` đi.
    state_store.set_kill_switch(False)
    assert state_store.is_kill_switch_on() is False

    # Và sau khi người tắt, chính vi phạm cũ được phép bật lại — nếu không thì
    # một lần tắt tay sẽ vô hiệu hoá cổng này vĩnh viễn.
    assert state_store.arm_kill_switch_if_drawdown_breached() is True
    assert state_store.is_kill_switch_on() is True


def main():
    tests = [
        test_drawdown_below_threshold_leaves_the_switch_off,
        test_drawdown_at_threshold_arms_the_switch_and_names_the_numbers,
        test_a_recovered_account_does_not_disarm_the_switch,
        test_a_healthy_ledger_does_not_disarm_a_switch_someone_else_armed,
        test_only_an_explicit_call_turns_the_switch_off,
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
