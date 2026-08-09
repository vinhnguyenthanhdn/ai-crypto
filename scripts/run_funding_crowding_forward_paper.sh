#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$(pwd)"

.venv/bin/python3 scripts/run_btc_spot_trend_forward_paper.py \
  --db data/state_btc_spot_trend_forward.db \
  --status data/backtests/btc_spot_trend_forward_status.json \
  --initial-equity 250

.venv/bin/python3 scripts/run_funding_crowding_forward_paper.py \
  --db data/state_funding_crowding_forward.db \
  --status data/backtests/funding_crowding_forward_status.json \
  --initial-equity 250

exec .venv/bin/python3 scripts/verify_composite_forward_promotion.py \
  --root . \
  --output data/backtests/composite_forward_status.json
