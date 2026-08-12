# Contributing

Thanks for considering a contribution. This project cares more about whether a
result is *true* than whether it is *good*, so the rules below are mostly about
protecting the integrity of measurements.

## The one rule that matters most

**A rejection is a result. Do not delete it, and do not tune your way out of it.**

Most of this repository is a record of strategies that did not work. That record is
the main asset. If your change makes a previously-rejected result look positive, the
burden is on you to show it is a genuine bug fix and not a subtle relaxation of a
gate.

## Methodology ground rules

These apply to any change that touches a strategy, a backtest, or a cost model.

1. **Select on train only.** Parameters, thresholds, filters, and features are chosen
   using the training split. Never look at validation or test before the contract is
   frozen.
2. **Freeze the contract before opening a holdout.** Write the contract down —
   parameters, fill rule, cost assumption, horizon — then run it. A holdout you have
   already seen is not a holdout.
3. **Report costs explicitly and stress them.** Every result states its round-trip
   cost assumption, and must survive at least 2× that cost to be considered.
   Measured cost values live in `docs/execution-cost.md`.
4. **Signals must be causal.** Features may only use data available at decision time.
   Fills happen at the next open or later, never at the signal bar's close.
5. **Count independent risk episodes, not tickets.** Tranches, rebalances, spread
   legs, and re-entries within the same setup are one episode. Reporting ticket
   counts as if they were independent bets is the most common way to fool yourself
   here.
6. **State your sample size honestly.** With ~29,000 configurations already searched
   across this data, a test-split profit factor near 1.0 is inside selection noise.
   Say so.

## Where to start

Issues labelled `good first issue` are self-contained bugs with a known location and
a clear correct behaviour — most are drawn from `docs/code-audit.md`, which lists
confirmed defects with file and line references.

Higher-impact work is labelled `help wanted`:

- **Multiple-testing corrections.** There is no deflated Sharpe, no White reality
  check, no PBO anywhere in the codebase. Adding them would change how several
  existing conclusions should be read.
- **Multi-asset falsification of the frozen contract.** The one strategy with
  positive gross edge has only 41 independent excursions. Running the frozen contract
  unchanged across a point-in-time liquid universe tests it without adding search
  burden.
- **Challenging a rejection.** Every contract and artifact is documented. If you
  believe a family was rejected for the wrong reason, reproduce it and show the work.

## Code layout and trust levels

Before relying on any number, read `docs/code-audit.md`. Two code paths coexist with
different trust levels:

- **Research path** (`scripts/discover_*.py`, `scripts/analyze_*.py`) — independently
  verified; produces the artifacts in `data/backtests/`.
- **Runtime path** (`src/run.py`, `src/backtest/engine.py`,
  `src/indicators/technical.py`) — has confirmed defects, catalogued with line
  references.

Fixes to the runtime path are very welcome. If your fix changes any existing
published number, say which artifacts are invalidated.

## Documentation

Docs follow a single-source-of-truth rule: each fact lives in exactly one file.

| File | Contents |
|---|---|
| `docs/decisions.md` | Stable architectural decisions and the objective function |
| `docs/backtest-results.md` | Every experiment run, including all rejections |
| `docs/inprogress.md` | Active experiments and blocking work |
| `docs/todo.md` | Not-yet-started tasks and known risks |
| `docs/execution-cost.md` | Measured fees, slippage, tax, venue constraints |
| `docs/code-audit.md` | Per-branch code trust levels and confirmed bugs |

When you add a result, put it in `backtest-results.md` — not in a new file, and not
duplicated across several. When something moves from "in progress" to "decided",
move it rather than copying it.

Write documentation as self-contained current state, not as a changelog. Avoid
"compared to last time" or "updated in v2" phrasing.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Before opening a pull request:

```bash
PYTHONPYCACHEPREFIX=/tmp/ai-crypto-pycache .venv/bin/python -m compileall -q src scripts
for f in scripts/test_*.py; do PYTHONPATH=. .venv/bin/python "$f" || echo "FAIL $f"; done
git diff --check
```

CI runs every file matching `scripts/test_*.py`, so a new test file is picked up with
no workflow change. Two things it needs to run standalone: the repository root on
`PYTHONPATH` (the loop above sets it, as does CI), and a `__main__` block that calls
each test function.

## Never commit

- Credentials of any kind. API keys must be read-only; never grant trade or withdraw
  permissions.
- `.env`, `config/paper.env`, `config/dashboard_secret.json`
- Datasets, `data/backtests/` artifacts, or any `*.db`
- Logs

## Pull requests

Open an issue before large changes so the direction can be discussed. In the PR
description, state what you changed, how you verified it, and — if it touches
measurement — which existing results are affected.
