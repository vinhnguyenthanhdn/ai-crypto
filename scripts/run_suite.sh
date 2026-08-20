#!/usr/bin/env bash
#
# Run every scripts/test_*.py and report how many ran.
#
# One script rather than one copy per caller: CI and the container image both
# need this loop, and two copies of it would drift the way every other pair of
# hand-kept lists in this repository has. Collection is a glob, never a list of
# filenames, and an empty glob is a failure — a suite that runs nothing must not
# be able to report success.
#
# The interpreter is ${PYTHON:-python}: CI and the image both put the right one
# on PATH, and a checkout with a .venv can pass PYTHON=.venv/bin/python.
#
# Group markers are emitted only under GitHub Actions, so local and container
# output stays readable.
set -euo pipefail
shopt -s nullglob

cd "$(dirname -- "$0")/.."
export PYTHONPATH="${PYTHONPATH:-$PWD}"

group_start() { [ -n "${GITHUB_ACTIONS:-}" ] && echo "::group::$1" || echo "--- $1"; }
group_end() { [ -n "${GITHUB_ACTIONS:-}" ] && echo "::endgroup::" || true; }

files=(scripts/test_*.py)
if [ ${#files[@]} -eq 0 ]; then
  echo "No test file matched scripts/test_*.py — the suite must never be empty."
  exit 1
fi

failed=()
for f in "${files[@]}"; do
  group_start "$f"
  if "${PYTHON:-python}" "$f"; then
    echo "PASS $f"
  else
    echo "FAIL $f"
    failed+=("$f")
  fi
  group_end
done

echo "Ran ${#files[@]} test file(s), ${#failed[@]} failed."
if [ ${#failed[@]} -gt 0 ]; then
  printf 'FAILED: %s\n' "${failed[@]}"
  exit 1
fi
