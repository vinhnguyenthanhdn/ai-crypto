#!/bin/bash
set -euo pipefail

project_root="$(cd "$(dirname "$0")/.." && pwd)"
runtime_root="/Users/administrator/Library/Application Support/ai-crypto-funding-forward"
launch_agent="/Users/administrator/Library/LaunchAgents/com.ai-crypto.funding-crowding-forward.plist"
label="com.ai-crypto.funding-crowding-forward"
agent_uid="$(id -u)"

mkdir -p "$runtime_root/scripts" "$runtime_root/src/engine" \
  "$runtime_root/data/backtests" "$runtime_root/data/strategy_packages" \
  "$runtime_root/logs"

# Only deploy the no-order observer and its deterministic decision dependencies.
# Secrets and the live runtime configuration are intentionally not copied.
/usr/bin/rsync -a "$project_root/scripts/run_funding_crowding_forward_paper.py" \
  "$project_root/scripts/run_btc_spot_trend_forward_paper.py" \
  "$project_root/scripts/run_funding_crowding_forward_paper.sh" \
  "$project_root/scripts/check_funding_crowding_forward.py" \
  "$project_root/scripts/verify_composite_forward_promotion.py" "$runtime_root/scripts/"
/usr/bin/rsync -a "$project_root/src/__init__.py" "$project_root/src/config.py" \
  "$project_root/src/state_store.py" "$runtime_root/src/"
/usr/bin/rsync -a "$project_root/src/engine/__init__.py" \
  "$project_root/src/engine/btc_spot_trend.py" \
  "$project_root/src/engine/funding_crowding.py" \
  "$project_root/src/engine/trend_sentiment.py" "$runtime_root/src/engine/"
/usr/bin/rsync -a \
  "$project_root/data/backtests/multiasset_funding_crowding_5y.json" \
  "$project_root/data/backtests/composite_btc_trend_funding_crowding_5y.json" \
  "$project_root/data/backtests/funding_crowding_runtime_parity_5y.json" \
  "$project_root/data/backtests/funding_crowding_paper_5y.json" \
  "$project_root/data/backtests/btc_spot_trend_paper_9y.json" \
  "$runtime_root/data/backtests/"
/usr/bin/rsync -a \
  "$project_root/data/strategy_packages/composite_btc_trend_funding_crowding_v1.json" \
  "$runtime_root/data/strategy_packages/"
/usr/bin/rsync -a "$project_root/config/com.ai-crypto.funding-crowding-forward.plist" \
  "$launch_agent"
chmod +x "$runtime_root/scripts/run_funding_crowding_forward_paper.sh"

if [ ! -x "$runtime_root/.venv/bin/python3" ]; then
  /opt/homebrew/bin/python3 -m venv "$runtime_root/.venv"
fi
"$runtime_root/.venv/bin/python3" -m pip install --disable-pip-version-check -q \
  -r "$project_root/config/funding_crowding_forward_requirements.txt"

plutil -lint "$launch_agent"
launchctl bootout "gui/${agent_uid}/${label}" >/dev/null 2>&1 || true
launchctl bootstrap "gui/${agent_uid}" "$launch_agent"
launchctl kickstart -k "gui/${agent_uid}/${label}"
echo "Installed ${label} in PAPER_NO_ORDER mode at ${runtime_root}"
