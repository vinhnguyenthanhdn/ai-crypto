# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Version numbers below `1.0.0` mean the public API of `src/` may change without a
deprecation cycle. What will not change is the research contract: no result is
reported unless it can be regenerated from the scripts in this repository.

## [Unreleased]

### Added

- A `Dockerfile` and a `container` CI job. The image pins the interpreter and the
  dependency set and runs the regression suite offline; CI builds it on every push
  and asserts three things about the result — the suite count matches the checkout
  with `--network none`, the container is not root and cannot write its own source,
  and no `config/` or `data/` directory is present. The image has never been
  deployed and live mode is not exercised by it; README says so in the same place
  it says how to run it.
- `scripts/run_suite.sh`, one collection loop shared by CI and the image instead of
  a copy in each. Collection stays a glob and an empty glob is still a failure.

- A `compose.yaml` and a `compose` CI job, which move the claim from "the image
  builds" to "the service comes up". The dashboard runs with a container
  healthcheck against `/api/session`, the one route that answers before login;
  `docker compose up --wait` fails the build if it never reports healthy. CI then
  confirms the verdict from outside the container — `docker inspect` reports
  `healthy` and the published port returns the expected body — and checks that the
  session secret lands in the named volume rather than in the working tree.
  Nothing here is deployed anywhere and Flask's development server is still a
  development server; README states the boundary.

### Changed

- `scripts/dashboard_server.py` reads its bind address and port from
  `DASHBOARD_HOST` and `DASHBOARD_PORT`. Defaults are unchanged (`127.0.0.1:8787`);
  a container needs `0.0.0.0` because loopback inside a container is not the host's
  loopback, and a published port would otherwise reach nothing.

- A `trials` count in the JSON artifact and printed summary of the three search
  scripts that did not carry one: `discover_bottom_entry_rules.py`,
  `discover_fast_derivatives_gate.py`, `discover_fast_multivariate.py` (#6). With
  29,373 configurations already searched across this repository and no multiple-testing
  correction anywhere in `src/` or `scripts/`, a reader had no way to see how many
  configurations a given result was selected from. No selection logic, gate, threshold
  or metric changes — `git diff` against `src/` is empty.

### Fixed

- CI compiled `scripts/` without ever importing them, so a broken import in a
  non-test script shipped green (#23). `scripts/test_script_imports.py` now
  importlib-loads scripts that have a `__main__` guard and AST-resolves the
  rest (notably `plot_social_preview.py`, which writes a PNG at import)
  (contributed by `AshSgDe29071999`).
- That first import check only resolved imports written at the top of a file, so a
  broken import inside a function body still shipped green — including
  `scripts/train_entry_model.py`, whose `joblib` import is deferred to the `--out`
  path and reached by no test (#25, contributed by `dchaudhari7177`). Every import in
  a script is now resolved wherever it sits, and scripts with a `__main__` guard get
  that check in addition to being imported.

### Changed

- The script import check exempts an import whose own `except ImportError` handler says it
  may fail, so declaring a dependency optional cannot be turned into a hard requirement by
  the checker. Nothing in `scripts/` uses that pattern today, and a test says so, so the day
  one does it shows up as a decision rather than as a missing module.
- `find_recent_swing_low` and `find_recent_swing_high` compared a candidate bar with
  `<=` / `>=`, so in a flat price region every bar qualified and the first one scanned
  was returned as a swing (#13, contributed by `mercael91`). A swing now has to be
  strictly beyond its neighbours, which is the rule `src/engine/support_resistance.py`
  already applied. Structural stop-loss and take-profit levels move as a result.
- A backtest that scores only the technical and regime layers could never reach the BUY
  threshold, and reported the result as zero trades rather than as an unreachable
  threshold (#5, contributed by `mercael91`). With the other layers pinned at
  `NEUTRAL_SCORE` the weighted total tops out at 69.0 against a threshold of 70.0, so
  the run was decided before it started.
- `matplotlib` was imported by `scripts/plot_gross_edge.py` and
  `scripts/plot_social_preview.py` but never declared in `requirements.txt`, so the
  README's invitation to regenerate the edge chart failed on a clean install with
  `ModuleNotFoundError` (#9, contributed by `stefannut`). Plotting stays optional at
  runtime: nothing under `src/` imports it.

### Added

- `scripts/test_swing_strictness.py` pins that behaviour from both directions: a flat
  series and a two-bar plateau yield no swing, and an isolated dip or peak still does.
- `max_attainable_score()` in `src/backtest/engine.py`, computed from `config.WEIGHTS`
  rather than hard-coded, and the `entry_possible` / `short_circuit_reason` fields that
  both backtest engines now report. A run that cannot produce an entry says so instead
  of returning an empty trade list.
- `scripts/test_threshold_reachable.py` covers both engines and both directions.
- `scope-guard` CI job (`scripts/scope_guard.py`): a pull request that removes a
  module-level public definition from `src/` fails unless the description names that
  definition (#16). Three layers of prose asking for the same thing had been ignored by
  three consecutive pull requests, so the rule moved to a place a machine decides.
- `docs/test-coverage-gaps.md` — which regression tests skip themselves on a clean
  checkout, and why, one entry per file. A green CI badge does not mean those files were
  graded, and the repository now says so in writing rather than leaving it to be
  discovered.
- `CODE_OF_CONDUCT.md` and `SECURITY.md`. The security policy separates a vulnerability
  from a losing backtest and states the read-only API key rule.

### Changed

- `scripts/test_composite_forward_promotion_e2e.py` runs on a clean checkout against a
  committed fixture instead of skipping for want of untracked artifacts (#19, contributed
  by `Abhishek4512009`). The fixture is regenerated by a script in the repository, and
  flipping a `passed` flag inside it aborts the test, so the test can still fail.
- README gained an `Architecture` section covering both pipelines where they meet at the
  strategy core, and a `Limitations` section that collects constraints previously spread
  across three documents. The architecture text states what the parity checks do prove
  (the execution loop) and what they cannot (the signal logic, which both paths share).

## [0.1.0] — 2026-08-14

First tagged release. This marks the point where the repository is reproducible
from a clean checkout and every claim in the README is backed by a script that a
reader can run.

### Research status

**No strategy has been promoted.** 29,373 parameter configurations across ~40
strategy families were searched, and none survived holdout evaluation once
measured execution costs were applied. The gross-edge distribution — returns
*before* any fees — sits on zero rather than to the right of the cost hurdle,
which points at absent edge rather than at expensive execution. This release
publishes the machinery that reached that verdict and the evidence that the
verdict is trustworthy; it does not publish a profitable strategy, because there
is not one to publish.

The engine never places real orders. Every execution path is simulated.

### Added

- Signal engine, backtest and paper-trading stack under `src/`: indicator layer,
  strategy cores, cost gate, event-sourced SQLite state store, position sizing
  and accounting, champion/challenger promotion pipeline, and notification hooks.
- Research and verification scripts under `scripts/`, including the grid searches,
  the parity reconciliations, the look-ahead sensitivity test, and the order book
  cost measurement.
- Regression suite of 16 `scripts/test_*.py` files, runnable as a single loop with
  `PYTHONPATH` set to the repository root. Tests that need backtest artifacts under
  `data/backtests/` skip themselves on a clean checkout instead of failing.
- GitHub Actions CI running `compileall` plus every `scripts/test_*.py` on Python
  3.10 and 3.12, a whitespace/conflict-marker check, and a `guard-secrets` job that
  fails the build if credentials, databases or datasets are ever tracked.
- Documentation set under `docs/`: `backtest-results.md` (result SSOT, including the
  failures), `decisions.md` (stable contracts), `code-audit.md` (adversarial
  self-verification), `execution-cost.md` (measured fees and spreads).
- `README.md` in English with the gross-edge chart as the headline result and
  `scripts/plot_gross_edge.py` to regenerate it.
- MIT license, `CONTRIBUTING.md` with the scope rule and the exact verification
  commands, a pull request template, and two issue templates.

### Fixed

- Swing confirmation window is now bounded by the decision index, removing
  look-ahead bias from `find_recent_swing_low/high` consumers (#11, contributed by
  `lakshanmuruganandam`). Measured effect: 6,746 of 40,000 sampled calls change
  result, so the fix is not a no-op.

### Changed

- CI now runs every file matching `scripts/test_*.py` rather than a hand-listed
  subset (#12). The previous list silently omitted `test_swing_lookahead.py`, the
  file that guards the behaviour fixed in #11.

### Known limitations

- Swing detection still uses non-strict comparison, so every bar inside a flat price
  region is reported as a swing point (#13). `src/indicators/support_resistance.py`
  uses strict comparison for the same concept; the two disagree.
- Two regression tests depend on artifacts under `data/backtests/`, which are not
  tracked. On a clean checkout they skip, so CI does not actually grade them.
- Results are measured on OKX and Binance BTC/USDT spot data. Nothing here has been
  validated on another venue or another asset class.

[Unreleased]: https://github.com/vinhnguyenthanhdn/ai-crypto/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/vinhnguyenthanhdn/ai-crypto/releases/tag/v0.1.0
