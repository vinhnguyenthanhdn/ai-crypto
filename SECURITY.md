# Security policy

## Supported versions

The `main` branch and the latest tagged release. There is no backport branch.

## Reporting a vulnerability

Use GitHub's private reporting: **Security → Report a vulnerability** on this repository. Please do not open a public issue for anything exploitable.

Include what you did, what happened, and what you expected. A rough timeline: acknowledgement within a week, a fix or an explanation within a month.

## The rule that matters most here

**Exchange API keys used with this repository must be read-only.** Never grant trade or withdraw permissions, and never commit a key. The system runs on public market data without any key at all; paper trading is simulated locally and needs no trading permission. A report that the code could place or authorise a real order is treated as a vulnerability, not a feature request.

## What is in scope

- Any path that would let the code place, cancel or authorise an order, or move funds.
- Credentials, database files or private datasets reaching the repository, the logs, or a notification channel. `.env`, `*.db` and `data/backtests/` are blocked by the `guard-secrets` job in CI; a way around that check is in scope.
- Reading or writing outside the repository directory from a value a user supplies (a symbol, a strategy name, a config path).
- File permissions or paths that expose local state to other users on the machine.

## What is not

- **Losing money on a strategy this repository produced.** Every result here is a backtest or paper trade, no strategy has passed holdout, and the release notes say so. Trading decisions are yours.
- **Third-party exchange or data-provider outages, rate limits and bad ticks.** Report those upstream. A crash caused by malformed upstream data is an ordinary bug — open a normal issue with the repro.
- Vulnerabilities in the dependencies listed in `requirements.txt` — report those to the projects that own them, though a note here about pinning is welcome.
