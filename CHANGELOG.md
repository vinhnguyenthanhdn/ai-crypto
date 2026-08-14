# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Version numbers below `1.0.0` mean the public API of `src/` may change without a
deprecation cycle. What will not change is the research contract: no result is
reported unless it can be regenerated from the scripts in this repository.

## [Unreleased]

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
