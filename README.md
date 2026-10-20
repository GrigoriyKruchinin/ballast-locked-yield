<div align="center">

<img src="docs/assets/logo.svg" alt="Ballast logo" width="180">

# Ballast

**Locked-yield router for stablecoin capital on DeFi L2s: fixed-rate entries only after cost and risk checks, paper-trading mode.**

Scans Pendle fixed-rate (PT) markets on Ethereum, Arbitrum and Base, lending-market parking rates (DefiLlama, RPC) and dated CEX futures basis.
Computes Net Locked Yield after gas, fees and slippage, scores asset and protocol risk against an incident catalog,
and verifies entries on an Anvil fork. Results go to PostgreSQL and Telegram.

[![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![mypy strict](https://img.shields.io/badge/mypy-strict-2A6DB2)](https://mypy.readthedocs.io/)
[![ruff](https://img.shields.io/badge/lint-ruff-D7FF64?logo=ruff&logoColor=black)](https://docs.astral.sh/ruff/)
[![docker compose](https://img.shields.io/badge/docker-compose-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![status](https://img.shields.io/badge/status-V0.1--dev%20research%20%26%20paper%20portfolio-orange)](todo.md)

</div>

> **Disclaimer.** "Locked" yield holds only if a position is kept to maturity and the underlying asset and protocol stay solvent and pegged.
> All figures produced by this project are estimates or historical results, not promises. Stage 1 moves no real funds.

## Features

- **Collector** (every 15 min): Pendle markets on 13 chains (Ethereum, Arbitrum, Base tradable in the paper portfolio; the rest observe-only),
  parking rates (Aave v3, Compound v3, Sky Savings via DefiLlama, core markets only), peg prices, protocol TVL, L1/L2 gas, dated futures basis
  (Deribit, OKX, Binance; data only).
- **Net Locked Yield (NLY)**: payoff at maturity minus entry/exit gas, Pendle fee (`feeRate x days/365`), swap fees and slippage
  (live Pendle quotes, AMM-only), all in `Decimal`. Re-pegs are never counted as profit.
- **Risk scoring**: whitelist with tiers (A fiat stables, B synthetic dollars, C forbidden), address pinning verified on-chain, protocol age/audits,
  admin keys, oracle dependency, TVL level and trend, peg deviation, sourced incident catalog (31 events since 2022), "too good to be true" filter.
  Entry threshold = best parking rate + tier premium + risk-score premium.
- **Allocator**: protocol cap 35% (also on parking), Pendle venue cap 70%, tier B cap 25% (15% per asset), max maturity 183 days,
  5% reserve, liquidity-capped sizing, gas-derived minimum position; every skip is logged with a reason.
- **Paper portfolios**: `all` and `l2` books with 10,000 USD each, parking accrual, maturity redemption, cost accounting.
- **Fork simulation**: daily entry/exit on an Anvil fork through the same transaction guards a live executor would use
  (whitelisted router, exact approvals, `minOut`, own recipient, per-tx limit); compared with the model (tolerance 0.3%).
- **Backtest**: replays the same decision code on hourly history of matured markets; tier premiums tuned on the first 60% of the window,
  metrics reported on the untouched last 40%.
- **Telegram**: new opportunities above threshold, risk events, daily and weekly digests (HTML, plain-language explanations).

## Quick start

Requirements: Docker with Compose, [uv](https://docs.astral.sh/uv/), Foundry (Anvil) for local fork runs. See
[docs/guides/stage-1-user-guide.md](docs/guides/stage-1-user-guide.md) (Russian) for step-by-step setup.

```bash
cp .env.example .env          # fill RPC_URL_* (Alchemy free tier), TELEGRAM_*, DB_PASSWORD
make up                       # postgres, redis, migrations, beat, collector, analytics, simulator, notifier
make ps                       # all services should be healthy
make kick                     # run one scan now (beat starts after the first interval)
make notify-test              # check Telegram
make backfill                 # load matured-market history for the backtest (hours; resumable)
make backtest                 # out-of-sample backtest -> docs/reports/backtest-YYYY-MM-DD.md
make simulate                 # fork-simulate current candidates
make report                   # Go/No-Go data report -> docs/reports/stage-1-go-no-go.md
```

Development:

```bash
uv sync
make check                    # ruff + mypy --strict + pytest
make verify-whitelist         # check pinned token addresses on-chain
```

## Architecture

```
            ┌──────────── beat (schedule) ────────────┐
            ▼                  ▼                       ▼
     collector (scan)   analytics (quotes,      simulator (Anvil fork)      notifier (Telegram)
            │            history, backtest)            │                          ▲
            ▼                  ▼                       ▼                          │
  sources: Pendle, DefiLlama, RPC gas, futures ──► normalize ──► NLY + cost model ─┤
                                                     │                             │
                                       risk rules ◄──┤──► allocator ──► paper portfolio
                                                     ▼                             │
                                       PostgreSQL (snapshots, NLY, risk, decisions,│
                                       positions, simulations, backtests, events) ─┘
```

- `ballast.domain`: Decimal money math, immutable models, narrow interfaces (`ILockedYieldSource`, `IChainClient`, `IRiskRule`,
  `ICostModel`, `IAllocator`, `ITxExecutor`).
- `ballast.sources` / `ballast.chains`: HTTP and JSON-RPC clients with retries, rate limits and secret redaction.
- `ballast.costs`, `ballast.nly`, `ballast.risk`, `ballast.allocation`: pure decision logic shared by live scans, backtest and simulation.
- `ballast.portfolio`: paper portfolio state machine and persistence. `ballast.execution`: tx guards and the Anvil executor.
- `ballast.pipeline`: composition root, scan runner, quotes, history backfill, fork simulation. `ballast.workers`: Celery tasks.
- Each container receives only the variables it needs: the Telegram token only reaches `notifier`, RPC keys only the chain-facing workers.

## Configuration

- `.env` (from `.env.example`): RPC URLs, Telegram, database, limits. Percent values are in percent units (`35` = 35%).
- `config/whitelist.yaml`: protocols, assets, tiers, pinned addresses, parking venues. Unknown assets are rejected.
- `config/incidents.yaml`: sourced incident catalog used by risk scoring and the incident adjustment.
- Agreed thresholds and every later change: [docs/decisions.md](docs/decisions.md). Data sources: [docs/research/data-sources.md](docs/research/data-sources.md).

## Roadmap

1. **Stage 1 (in progress):** research MVP with live collection (>= 4 weeks), backtest, fork checks, paper portfolio, Go/No-Go report.
2. **Stage 2:** production execution with real funds, only after a Go decision.
3. **Stage 3:** admin dashboard and analytics.

Progress: [todo.md](todo.md).
