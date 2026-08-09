#!/bin/bash
# WebSocket price/trade collector riêng cho instance Paper Trading —
# `run_paper.sh`/`_current_price()` đọc tick giá thật qua `state_store.get_last_tick()`,
# nếu nguồn dữ liệu đó (`collector_ws.py`) không chạy cho instance này thì tick
# luôn fallback về giá REST snapshot cũ (đứng yên suốt cửa sổ theo dõi).
#
# Phải source đúng config/paper.env để chạy đúng SÀN (EXCHANGE_ID) và ghi tick
# vào đúng DB_PATH riêng (data/state_paper.db) — không đụng data/state.db.
set -euo pipefail
cd "$(dirname "$0")/.."

export RUNTIME_ENV_PATH="config/paper.env"

.venv/bin/python3 -m src.collector_ws
