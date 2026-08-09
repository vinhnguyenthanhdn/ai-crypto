"""Health-check độc lập, được launchd lên lịch riêng với Rule Engine.

Đọc `run_health.last_run_at` và báo Telegram khi heartbeat quá cũ; chỉ gửi khi
chuyển trạng thái UNHEALTHY/phục hồi. Health model chi tiết hơn nằm trong
`TODO-HEARTBEAT`.

Usage: python scripts/health_check.py [--db-path PATH] [--label TEN]
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, state_store  # noqa: E402
from src.notify import telegram  # noqa: E402


def _age_minutes(iso_ts: str) -> float:
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iso_ts)).total_seconds() / 60


def _send(label, text):
    sent = telegram.send_message(text)
    if not sent:
        print(f"[health_check] gửi Telegram thất bại: {text}", file=sys.stderr)


def _handle_unhealthy(label, alert_kv_key, detail):
    print(f"[health_check] {label}: UNHEALTHY — {detail}")
    already_alerted = state_store.get_kv(alert_kv_key) == "1"
    if not already_alerted:
        _send(label, f"⚠️ [{label}] Health-check: hệ thống có thể đã dừng — {detail}")
        state_store.set_kv(alert_kv_key, "1")


def _handle_healthy(label, alert_kv_key, age):
    print(f"[health_check] {label}: OK — lần chạy gần nhất cách đây {age:.1f} phút")
    was_alerted = state_store.get_kv(alert_kv_key) == "1"
    if was_alerted:
        _send(label, f"✅ [{label}] Health-check: đã phục hồi, lần chạy gần nhất cách đây {age:.1f} phút")
        state_store.set_kv(alert_kv_key, "0")


def main():
    parser = argparse.ArgumentParser(
        description="Health-check độc lập — báo Telegram nếu quá lâu không có lần chạy nào."
    )
    parser.add_argument("--db-path", default=None, help="Mặc định: config.DB_PATH (.env)")
    parser.add_argument(
        "--label", default=None,
        help="Nhãn phân biệt instance trong message Telegram (mặc định config.STRATEGY_LABEL hoặc 'default')",
    )
    parser.add_argument(
        "--max-stale-minutes", type=float, default=None,
        help="Mặc định: config.HEALTHCHECK_MAX_STALE_MINUTES",
    )
    args = parser.parse_args()

    if args.db_path:
        config.DB_PATH = Path(args.db_path)
    label = args.label or config.STRATEGY_LABEL or "default"
    max_stale = (
        args.max_stale_minutes if args.max_stale_minutes is not None
        else config.HEALTHCHECK_MAX_STALE_MINUTES
    )
    alert_kv_key = f"healthcheck_alerted_{label}"

    try:
        health = state_store.get_run_health()
    except Exception as e:
        # DB không đọc được (mất file, corrupt...) — không dedupe được qua kv_store
        # của chính DB đang lỗi, báo thẳng mỗi lần thay vì im lặng.
        print(f"[health_check] {label}: lỗi đọc DB {config.DB_PATH}: {e}", file=sys.stderr)
        _send(label, f"⚠️ [{label}] Health-check: không đọc được DB ({config.DB_PATH}): {e}")
        sys.exit(1)

    if health is None:
        _handle_unhealthy(label, alert_kv_key, "chưa từng ghi nhận lần chạy nào")
        return

    age = _age_minutes(health["last_run_at"])
    if age > max_stale:
        _handle_unhealthy(
            label, alert_kv_key,
            f"lần chạy gần nhất cách đây {age:.0f} phút (> {max_stale:.0f} phút), "
            f"last_run_ok={health['last_run_ok']}",
        )
    else:
        _handle_healthy(label, alert_kv_key, age)


if __name__ == "__main__":
    main()
