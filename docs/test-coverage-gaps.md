# Tests that do not run on a clean checkout

CI runs every `scripts/test_*.py` on Python 3.10 and 3.12. Two of them print `SKIP` and exit 0 on a fresh clone because they need artifacts that `.gitignore` and the `guard-secrets` CI job deliberately keep out of the repository. A green CI therefore does **not** mean these two were graded.

| Test | Needs | Why it is not committed | Status |
|---|---|---|---|
| `scripts/test_staggered_paper_replay_e2e.py` | `data/backtests/binance_btcusdt_spot_5m_flow_9y.json.gz` | A 42 MB gzipped 5-minute flow cache covering nine years. Too large for the repository, and a truncated slice would not reproduce the trade-for-trade parity the test asserts. | Will keep skipping. Run it locally after rebuilding the cache with `scripts/fetch_binance_5m_flow.py`. |
| `scripts/test_composite_forward_promotion_e2e.py` | four `data/backtests/*.json` result files and one `data/strategy_packages/*.json` package | Size is not the obstacle here — the five files total roughly 25 KB. The obstacle is location: `guard-secrets` rejects anything under `data/backtests/`, on purpose, so no pull request can add them there. | Fixable. Tracked in [#18](https://github.com/vinhnguyenthanhdn/ai-crypto/issues/18); the fix is a fixture directory outside `data/` plus the `--root` flag the verifier already accepts. |

The rule this table follows: a test that cannot run for a contributor should say so out loud and name what it needs, rather than quietly passing. If you add a test that depends on a generated dataset, follow the same shape — check for the artifact, print `SKIP` with the path, and add a row here.
