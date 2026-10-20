# todo.md: Ballast

Legend: `[x]` done and verified, `[ ]` open. Parameters: [docs/decisions.md](docs/decisions.md).

## Setup

- [x] Parameters agreed and recorded in `docs/decisions.md` (targets, whitelist, limits, chains)
- [x] `CLAUDE.md`, `.claude/` agents (implementation, security & code quality critic) and skills (check-code-quality, run-tests, audit-quality, run-backtest)
- [x] Git, `.gitignore`, `.env.example`; README header and `docs/assets/logo.svg`
- [x] User guide `docs/guides/user-guide.md` (Russian)

## Core

### Foundation
- [x] `pyproject.toml` (uv, Python 3.12), ruff, mypy strict, pytest, pytest-asyncio, hypothesis; `uv.lock`; `Makefile`
- [x] Package layout, modular Pydantic Settings with per-role factories
- [x] Asset/protocol whitelist with tiers and limits; unknown = rejected; addresses pinned and verified on-chain (13/13)
- [x] Domain model, Decimal money primitives, narrow interfaces
- [x] Structured logging with secret redaction

### Storage
- [x] SQLAlchemy async models, repositories, Alembic migrations (verified up/down/check on Postgres 16)

### Data collection
- [x] Data source research (`docs/research/data-sources.md`)
- [x] Pendle markets (13 chains; Ethereum/Arbitrum/Base analysed in depth), prices, history, convert quotes
- [x] Lending rates (Aave v3, Compound v3, Sky Savings; core markets only), protocol TVL, peg prices
- [x] Gas per chain incl. rollup L1 fee; dated futures basis (Deribit, OKX, Binance)
- [x] Celery schedule: scan every 15 min, quotes hourly, history backfill and fork simulation daily, digests

### Analysis
- [x] Cost model calibrated on Pendle quotes and fork runs; NLY calculator with property tests
- [x] Risk rules, scorer, exposures; sourced incident catalog (31 entries)
- [x] Greedy allocator with exposure caps; decision log with reasons
- [x] Paper portfolios (`all`, `l2`) with value-conservation tests and sticky lending positions
- [x] Fork simulation on Anvil behind `TxGuard` (model vs fork +0.01%)
- [x] Out-of-sample backtest reusing the live decision code; benchmark targets
- [x] Reports: `ballast backtest`, `ballast report`; Telegram alerts and digests

### Infrastructure
- [x] Dockerfile and `docker-compose.local.yml` with healthchecks and per-role environment
- [x] CI: ruff, mypy, migrations on Postgres, pytest, compose config, image build

### Research
- [x] Backtest 2025-04-20 → 2026-10-19 (`docs/reports/backtest-2026-10-20.md`); results in README

## Polish & docs

- [ ] Refresh research results after >= 4 weeks of live collection (`make report`, `make backtest`) and update README exactly
- [ ] Run the `audit-quality` skill and fix CRITICAL/HIGH findings
- [ ] On-chain cross-check of Aave lending rates (`getReserveData`, ray APR -> APY) against DefiLlama
- [ ] Cost cross-chain moves (USDC via CCTP, USDS bridges) between lending venues and positions on different chains
- [ ] Monitors: contract pause and oracle status (on-chain reads)
- [ ] Per-market drill-down in reports (best NLY vs threshold over time for each whitelisted market)
- [ ] Usage examples in `docs/` (adding a source via `ILockedYieldSource`, adding a risk rule)
- [ ] Raise test coverage of `pipeline` and `workers` (integration tests against Postgres)
