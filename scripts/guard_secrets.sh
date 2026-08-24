#!/usr/bin/env bash
# Refuse committed credentials, datasets and databases.
#
# The tree to inspect is an argument rather than the current directory, because
# this guard spends its whole life reporting that nothing is tracked: none of
# the names it refuses can appear in a repository that has never committed one.
# A pass proves nothing on its own. Pointing the same code at a scratch
# repository holding one of those names is what turns the pass into evidence,
# and that is what `--self-test` does.
set -euo pipefail

# One alternative per thing that must never be committed. Keep them here only:
# the self-test walks the case list below, so an alternative added without a
# case that exercises it is an alternative nobody has seen match.
FORBIDDEN_RE='^\.env$|dashboard_secret|config/paper\.env|\.db$|^data/backtests/|\.pem$|\.key$'

scan() {
  local root="$1" bad
  cd "$root"
  bad=$(git ls-files | grep -Ei "$FORBIDDEN_RE" || true)
  if [ -n "$bad" ]; then
    echo "These files must never be committed:"
    echo "$bad"
    return 1
  fi
  echo "No credentials, databases or datasets tracked."
}

# Each case is a path the pattern above is meant to catch. Paths, not contents:
# this guard reads the file list, so planting a file with the right name is the
# whole violation.
CASES="\
dotenv:.env
dashboard-secret:config/dashboard_secret.json
paper-env:config/paper.env
sqlite:state/trades.db
backtest-dataset:data/backtests/2026.csv
certificate:deploy/server.pem
private-key:deploy/server.key"

self_test() {
  local entry name path tmp status
  for entry in $CASES; do
    name="${entry%%:*}"
    path="${entry#*:}"
    tmp="$(mktemp -d)"
    (
      cd "$tmp"
      git init -q .
      git config user.email guard@example.com
      git config user.name guard
    )
    mkdir -p "$tmp/$(dirname "$path")"
    printf 'planted by the guard self-test\n' > "$tmp/$path"
    # --force, or a .gitignore-shaped name would never reach the index and the
    # case would pass for the wrong reason.
    git -C "$tmp" add --force -- "$path"
    status=0
    ( scan "$tmp" ) >/dev/null 2>&1 || status=$?
    rm -rf "$tmp"
    if [ "$status" -eq 0 ]; then
      echo "the guard accepted a tree tracking '$path' ($name) — it can no longer go red" >&2
      return 1
    fi
    echo "rejected as expected: $name ($path)"
  done
  echo "self-test: the guard still fires on every name it claims to refuse"
}

case "${1:---help}" in
  --self-test) self_test ;;
  --help) echo "usage: $0 <tree> | --self-test" >&2; exit 2 ;;
  *) scan "$1" ;;
esac
