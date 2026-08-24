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

## Ways to contribute that aren't code

Writing the patch is often the cheapest part of a change here. Deciding whether a
number can be trusted is not, and that work is welcome on its own terms:

- **Reproducing a reported defect, or failing to.** An issue that says "still happens
  on `main` as of <commit>, here is the output" is worth more than a second opinion,
  and so is "I could not reproduce this, here is what I ran instead".
- **Challenging a published result.** Every rejection in this repository is meant to
  be attackable. If the artifacts do not support the conclusion drawn from them, say
  so with the run you did.
- **Reviewing an open pull request.** Especially one that touches
  `src/backtest/engine.py` or `src/indicators/technical.py`, where a change can move
  numbers that are already published.
- **Reporting what the documented commands do on a setup that is not macOS with
  Python 3.12** — that is the only environment they are checked on outside CI.

These are credited the same way a patch is: `CHANGELOG.md` names the person whose
report or review caused the change, not only the author of the commit.

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
PYTHONPYCACHEPREFIX=/tmp/ai-crypto-pycache .venv/bin/python -m compileall -q $(git ls-files '*.py')
PYTHON=.venv/bin/python bash scripts/run_suite.sh
git diff --check
```

Both commands are the ones CI runs, so what passes here is what passes there —
`compileall` walks every tracked `.py`, and `run_suite.sh` is the single collection
loop shared by CI and the container image.

CI runs every file matching `scripts/test_*.py`, so a new test file is picked up with
no workflow change. Two things it needs to run standalone: a `__main__` block that
calls each test function, and — if it imports from `src/` or `scripts/` — the
repository root inserted by the file itself:

```python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

`run_suite.sh` strips `PYTHONPATH` before each file for that reason: `python
scripts/test_x.py` is the first thing anyone tries, and a file that only imports
under an exported `PYTHONPATH` fails there while passing in CI.

Test files must live in `scripts/` and be named `test_*.py`; CI rejects files with
that name outside `scripts/` instead of running them. Move such a file to `scripts/`
or rename it. The `tests/` directory is reserved for fixtures.

Tests that need `data/backtests/` artifacts cannot run on a clean checkout, because
those artifacts are never committed. Such a test checks for its inputs and prints
`SKIP` instead of failing — locally, where the artifacts exist, it runs in full.

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

### Stay inside the scope of the issue

A pull request that fixes one behaviour touches the lines implementing that
behaviour, plus a test. It does not rewrite the module around them. This matters more
here than style preference: a rewritten file cannot be reviewed against the issue it
claims to close, and it silently drops guards that earlier fixes put in.

Concretely, a change gets sent back when it:

- removes a public function, or changes a signature other callers use — check with
  `grep -rn "function_name" src scripts` before touching one;
- replaces an implementation with a differently-shaped one instead of editing it;
- adds indicators or helpers the repository already gets from `ta`;
- bundles unrelated cleanups with the fix.

Two checks catch nearly all of this before review:

```bash
git diff --stat main...HEAD                                  # is the diff the size of the fix?
git diff main...HEAD | grep '^-.*def '                       # any public function removed?
```

CI enforces the first of these. The `scope-guard` job (`scripts/scope_guard.py`) fails a
pull request that removes a module-level public definition from `src/` without naming
that definition in the pull request description. Removing one is allowed; removing one
silently is not.

If a fix genuinely needs a wider change, open an issue describing the wider change
first. Splitting it into a minimal fix now and a refactor later gets both merged
faster than one pull request doing both.

### Record the change in the changelog

A pull request that changes anything a user of this repository would notice — behaviour,
a command, a documented result, a guarantee about what CI grades — adds a line to the
`## [Unreleased]` section of [`CHANGELOG.md`](CHANGELOG.md), under `Added`, `Changed`,
`Fixed` or `Known limitations`.

Write what changed and what it means for someone reading a result, not the file names:
"the E2E test now runs on a clean checkout" rather than "updated test file". Contributions
are credited there by issue number and GitHub handle, which is the entry that survives
into the release notes.

Pure refactors, typo fixes and internal test additions that change nothing observable do
not need an entry.

### Verify by running, not by reading

Every claim in a pull request should come with the command that produced it. For a
change to an indicator, a signal, or a cost model, include how many calls change
result out of how many — run the old and the new implementation side by side over
randomly generated OHLC series and count differences. A change that measures as a
no-op is worth knowing about before merge, not after.
