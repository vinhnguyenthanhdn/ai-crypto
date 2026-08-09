# AI Crypto Signal Engine

A research platform for systematically discovering — and rigorously falsifying —
crypto trading strategies, with event-sourced paper trading, implementation-parity
verification, and a full audit trail from raw candle to logged trade.

The system never places real orders. Every execution path is simulated.

## Why this repository is worth reading

Most public trading repos show you a backtest with a nice equity curve. This one
shows you the **machinery that decides whether an equity curve is real**, and then
applies it honestly — including to its own results.

- **9 years of verified market data.** 942,025 5-minute candles plus 1,508
  checksum-verified derivatives archives, with SHA-256 dataset hashes recorded in
  every artifact.
- **Implementation parity, verified trade-for-trade.** Research reference engines
  and production strategy cores are written independently, then reconciled: 963/963,
  515/515, 1,079/1,079 trades matched with zero mismatches, and equity agreeing to
  8 decimal places.
- **Accelerated paper replay through the real lifecycle.** 3,277 days of history
  driven through the *production* SQLite state store, position sizing, and
  accounting — only the market clock is simulated. 77 ENTRY / 77 EXIT / 77 ledger
  rows, zero orphaned entries, zero positions left open.
- **Adversarial self-verification.** The engine was audited by reimplementing its
  contracts from scratch against raw data. The price cache matched the venue API
  **bit-for-bit** across 3,277 days; a rejected contract reproduced across
  **480/480** grid points; costs were confirmed charged exactly once
  (−0.29985% measured on a flat round trip against a stated 0.30%).
- **A look-ahead sensitivity test that actually has teeth.** Deliberately injecting
  look-ahead into the passing contract inflates training return from **+563% to
  +4,201%** — demonstrating the test could detect the bug, and that the committed
  result sits on the honest side of it.
- **Costs measured, not assumed.** 4,695 order book samples showed the real
  half-spread is 0.0077 bps, not the 5 bps the config assumed — and base-tier venue
  fees were pulled from official sources, revealing the project's own round-trip
  cost assumption was ~3× too high.
- **Every experiment documented, including the failures.** ~40 strategy families,
  100 result artifacts, each with its contract, dataset range, cost assumptions, and
  verdict.

## Engineering highlights

- **Event-sourced state.** Append-only `event_log`, `feature_snapshot`, and an
  idempotent `equity_ledger` keyed by trade ID. Position and risk are computed from
  real equity, not notional assumptions.
- **Feature lineage.** Every snapshot records source exchange, market type, symbol,
  timeframe, transformation version, and the strategy package that consumed it — so
  any logged decision can be reconstructed.
- **Atomic run locking.** SQLite `BEGIN IMMEDIATE` lease with owner token and
  heartbeat; a dead process's lease is reclaimed immediately rather than waiting out
  a stale timeout.
- **Frozen contracts and cost-stress gates.** Parameters are selected on the training
  split only, the contract is frozen, then validation and test are opened once. Every
  candidate must survive round-trip costs stressed to at least 2× base.
- **Market-data freshness enforcement.** Stale collector ticks hard-fail or force a
  fresh fetch rather than silently reusing an old snapshot.
- **Operational runtime.** launchd-managed scheduler, WebSocket collector with gap
  marking, independent health check, and a local dashboard with score/price timeline
  and trade history.

## Honest status: no validated edge yet

This is stated plainly because it is the most important thing to know, and because
publishing it is the point.

After searching **29,373 parameter configurations** across ~40 strategy families,
**no strategy has passed the project's own promotion gates**, and live forward paper
trading has produced **zero closed trades** to date.

The negative result is itself the finding, and it is quantified rather than
hand-waved:

- Median gross edge — return *before any fees* — across 13,654 rejected
  configurations is **−0.021%**, statistically indistinguishable from zero.
- Recomputed independently from raw candles, common intraday signals (EMA cross,
  breakout, momentum, taker imbalance) return **+0.01% to +0.05%** forward, versus
  the asset's own baseline drift of **+0.019%**. The signals are the drift.
- Consequently, **reducing trading costs to zero would not rescue these families.**
  There is no gross edge to rescue.
- Maker execution does not fix it either: L2 replay measured markout of **−15 to
  −29 bps** against roughly 5.4 bps of fee savings. Adverse selection dominates.

| Finding | Evidence |
|---|---|
| Intraday directional signals have ~zero gross edge | 13,654 configs, median gross −0.021% |
| Cost assumptions were ~3× too high | Base-tier fee schedules + 4,695 book samples |
| The one contract with real gross edge is low frequency | 253 test tickets → 41 independent excursions, t-stat 0.57 |
| Backtest and live rule engine implement *different* strategies | `docs/code-audit.md` |

Negative results at this scale are rarely published. The search space is documented
well enough that you can avoid re-walking it.

## Two code paths, two trust levels

This distinction matters before relying on any number in the repository.

- **Research path** (`scripts/discover_*.py`, `scripts/analyze_*.py`) — produces
  every artifact in `data/backtests/`. Independently verified and **trusted**: data
  integrity, cost accounting, and causal correctness all reproduce against
  from-scratch reimplementations.
- **Runtime path** (`src/run.py`, `src/backtest/engine.py`,
  `src/indicators/technical.py`) — has confirmed defects, including a BUY threshold
  that is arithmetically unreachable and a look-ahead bug in the swing detector.
  These are catalogued with file and line references rather than quietly patched.

Full detail in `docs/code-audit.md`.

## Architecture

```text
Exchange → Collector → Feature Engine → Rule Engine → Risk Engine
→ Paper Execution State → Event/Trade Log → Report
```

The runtime is a pure rule engine. Entry models, RAG, and champion/challenger model
serving exist as scaffolding and do not participate in decisions.

```text
src/           Runtime: collector, indicators, decision/risk engines, state store
src/backtest/  Backtest and accelerated paper-replay engines
src/engine/    Strategy cores (staggered pullback, BTC spot trend, funding crowding)
scripts/       Research harness: discovery, analysis, validation, dashboard
docs/          Decisions, results, audits, open tasks
knowledge/     Human-readable model cards and lessons
```

Datasets and result artifacts are **not committed** — they are large and fully
reproducible from the fetch scripts.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

API keys must be **read-only**. Never grant trade or withdraw permissions. The
system works without keys using public market data. Telegram credentials are
optional.

## Running

```bash
# single pass
python3 -m src.run

# fast bar-close backtest scan
.venv/bin/python3 scripts/run_backtest.py

# tick-proxy paper-parity backtest
.venv/bin/python3 scripts/run_paper_backtest.py \
  --timeframe 1h --tick-timeframe 1m --days 180 --walk-forward 2 --no-mlflow

# local dashboard, binds 127.0.0.1:8787, session/password auth
.venv/bin/python3 scripts/dashboard_server.py
```

Scheduled paper trading runs under launchd on macOS. See `docs/decisions.md` for the
scheduling contract.

## Documentation

| File | Contents |
|---|---|
| `docs/decisions.md` | Stable architectural decisions and the objective function |
| `docs/backtest-results.md` | Every experiment run, including all rejections |
| `docs/inprogress.md` | Active experiments and blocking work |
| `docs/todo.md` | Not-yet-started tasks and known risks |
| `docs/execution-cost.md` | Measured fees, slippage, tax, venue constraints |
| `docs/code-audit.md` | Per-branch code trust levels and confirmed bugs |

Documentation follows a single-source-of-truth rule: each fact lives in exactly one
file. Please preserve that when contributing.

## Contributing

Contributions are welcome. The work that would help most, roughly in order:

1. **Fix the runtime-path bugs** in `docs/code-audit.md`. Several are self-contained
   and well-localized — a good first contribution.
2. **Add multiple-testing corrections.** There is currently no deflated Sharpe, no
   White reality check, no PBO. With ~29k configurations searched, any test-split
   profit factor near 1.0 is inside selection noise, and nothing in the codebase
   currently says so.
3. **Falsify the remaining candidate.** The staggered pullback contract has positive
   gross edge but only 41 independent excursions. Applying the frozen contract to a
   point-in-time multi-asset universe would test it without adding search burden.
4. **Challenge the negative results.** If you think a family was rejected for the
   wrong reason, every contract and artifact is here. Reproduce it and show the work.

Ground rules that keep results meaningful:

- Select parameters on the training split only. Never tune against a holdout you have
  already seen.
- Freeze the contract before opening validation or test.
- Report costs explicitly and stress them to at least 2× base.
- A rejection is a result. Do not delete it.
- Do not commit datasets, databases, credentials, or runtime config.

Open an issue before large changes so direction can be discussed.

## Disclaimer

This is research software — not financial advice, and not a trading product. It has
no demonstrated profitability. Cryptocurrency trading carries substantial risk of
loss. If you adapt this toward live execution you do so entirely at your own risk,
and you should read `docs/code-audit.md` first to understand what is known to be
broken.

## License

Not yet licensed. Until a license is added, default copyright applies and reuse is
not permitted.
