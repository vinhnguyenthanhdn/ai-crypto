"""CLI bật/tắt Kill Switch thủ công (xem plan-02.md mục 8 Risk Engine).

Dùng khi: hệ thống tự bật kill switch do chạm Max Drawdown, hoặc muốn dừng
tay hệ thống vì lý do khác. Tắt kill switch là quyết định thủ công, hệ thống
không tự tắt lại.

Usage:
    python scripts/kill_switch.py status
    python scripts/kill_switch.py on "lý do"
    python scripts/kill_switch.py off
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import state_store  # noqa: E402


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "status":
        on = state_store.is_kill_switch_on()
        print(f"kill_switch = {'ON' if on else 'OFF'}")
        if on:
            print(f"reason = {state_store.get_kill_switch_reason()}")
        print(f"max_drawdown_pct = {state_store.get_max_drawdown_pct()}")
    elif cmd == "on":
        reason = sys.argv[2] if len(sys.argv) > 2 else "Bật thủ công"
        state_store.set_kill_switch(True, reason=reason)
        print(f"Đã bật kill switch: {reason}")
    elif cmd == "off":
        state_store.set_kill_switch(False)
        print("Đã tắt kill switch")
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
