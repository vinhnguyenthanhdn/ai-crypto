# Tests that do not run on a clean checkout

CI runs every `scripts/test_*.py` on Python 3.10 and 3.12. One of them prints `SKIP` and exits 0 on a fresh clone because it needs an artifact that `.gitignore` and the `guard-secrets` CI job deliberately keep out of the repository. A green CI therefore does **not** mean this one was graded.

| Test | Needs | Why it is not committed | Status |
|---|---|---|---|
| `scripts/test_staggered_paper_replay_e2e.py` | `data/backtests/binance_btcusdt_spot_5m_flow_9y.json.gz` | A 42 MB gzipped 5-minute flow cache covering nine years. Too large for the repository, and a truncated slice would not reproduce the trade-for-trade parity the test asserts. | Will keep skipping. Run it locally after rebuilding the cache with `scripts/fetch_binance_5m_flow.py`. |

`scripts/test_composite_forward_promotion_e2e.py` used to be on this table ([#18](https://github.com/vinhnguyenthanhdn/ai-crypto/issues/18)); it now runs on a clean checkout thanks to the committed fixtures under `tests/fixtures/composite_forward/` (real artifacts still take precedence when present), so it is no longer listed as "not graded".

The rule this table follows: a test that cannot run for a contributor should say so out loud and name what it needs, rather than quietly passing. If you add a test that depends on a generated dataset, follow the same shape — check for the artifact, print `SKIP` with the path, and add a row here.
