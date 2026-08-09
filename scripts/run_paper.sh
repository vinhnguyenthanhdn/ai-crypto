#!/bin/bash
# Paper Trading instance riêng — khung/ngưỡng cụ thể đang chạy xem
# config/paper.env, sửa qua dashboard scripts/dashboard_server.py hoặc sửa tay
# file đó — KHÔNG hard-code timeframe/threshold trong tên file/script nữa vì
# dashboard cho đổi TIMEFRAME tự do, đặt tên cố định theo khung dễ gây hiểu nhầm).
#
# Dùng state DB + log riêng (KHÔNG đụng data/state.db của live 5m — 2 instance
# chạy độc lập, position_state không phân biệt khung nên bắt buộc phải tách DB).
set -euo pipefail
cd "$(dirname "$0")/.."

export RUNTIME_ENV_PATH="config/paper.env"

.venv/bin/python3 -m src.run
