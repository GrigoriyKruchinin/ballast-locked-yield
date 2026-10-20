<div align="center">

<img src="docs/assets/logo.svg" alt="Ballast logo" width="180">

# Ballast

**DeFi locked-yield analytics toolkit: monitors fixed-rate stablecoin yield, nets out every cost and scores the risk behind it.**

Collects Pendle fixed-rate (PT) markets on 13 chains, lending-market rates (Aave v3, Compound v3, Sky Savings via DefiLlama),
peg prices, protocol TVL, L1/L2 gas and dated futures basis (Deribit, OKX, Binance). Computes Net Locked Yield after gas,
fees and slippage, compares it with risk-free-ish parking against a sourced incident catalog, checks executability on an
Anvil fork and simulates a portfolio on live data. Results go to PostgreSQL, Markdown reports and Telegram.

[![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![mypy strict](https://img.shields.io/badge/mypy-strict-2A6DB2)](https://mypy.readthedocs.io/)
[![ruff](https://img.shields.io/badge/lint-ruff-D7FF64?logo=ruff&logoColor=black)](https://docs.astral.sh/ruff/)
[![docker compose](https://img.shields.io/badge/docker-compose-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![status](https://img.shields.io/badge/status-v1.0.0%20research%20toolkit-blue)](CHANGELOG.md)

</div>

> "Locked" (fixed) yield holds only if a position is kept to maturity and the underlying asset and protocol stay solvent and pegged.
> All figures below are historical or simulated estimates. The toolkit moves no funds and holds no keys.

## What it answers

- Which fixed-rate stablecoin markets exist right now, on which chains, and how deep are they?
- What does a fixed rate pay **after** entry and exit gas, Pendle fees, swap legs and price impact, for a given position size?
- Is the premium over plain lending large enough to pay for the asset's risk tier and its incident history?
- Would a disciplined allocator with hard exposure caps have beaten simple lending, historically and on live data?
- Do real transactions (simulated on a fork) settle at the modelled price?

## Features

- **Collector** (every 15 min): Pendle markets on 13 chains (Ethereum, Arbitrum and Base are analysed in depth, the other chains are
  recorded for coverage statistics), core lending markets only (no slashable staking pools or side instances), peg prices,
  protocol TVL, gas including rollup L1 fees, dated futures basis.
- **Net Locked Yield (NLY)**: payoff at maturity minus entry/exit gas, Pendle fee (`feeRate x days / 365`), swap fees and slippage
  (hourly Pendle quotes, AMM-only), computed in `Decimal`. Re-pegs are never counted as profit.
- **Risk scoring**: whitelist with tiers (A fiat-backed stables, B synthetic dollars, C rejected), token addresses pinned and verified
  on-chain, protocol age and audits, admin keys, oracle dependency, TVL level and trend, peg deviation, a sourced incident catalog
  (31 events since 2022) and a "too good to be true" filter. Entry threshold = best lending rate + tier premium + risk-score premium.
- **Allocator**: 35% per protocol (lending included), 70% per venue, tier B 25% (15% per asset), maturity up to 183 days, 5% reserve,
  liquidity-capped sizing, gas-derived minimum position. Every skipped opportunity is logged with its reason.
- **Paper portfolios** on live data: an all-chain book and an L2-only book of 10,000 USD each, with lending accrual, redemption at
  maturity and full cost accounting.
- **Fork simulation**: daily entry and exit through Pendle on an Anvil fork, behind transaction guards (whitelisted router, exact
  approvals, `minOut`, own recipient, size limit); results compared with the model (tolerance 0.3%) and with the quote.
- **Backtest**: replays the same decision code over hourly history of matured markets; tier premiums tuned on the first 60% of the
  window, metrics reported on the untouched last 40%.
- **Reports and alerts**: `docs/reports/backtest-*.md`, `docs/reports/research-report.md`, Telegram alerts and daily/weekly digests.

## Research results (October 2026)

### Backtest (out-of-sample)

Window 2025-04-20 → 2026-10-19 on Ethereum, Arbitrum and Base: 289 stable Pendle markets with hourly history, 24 of them on
whitelisted assets (mostly USDe/sUSDe, some USDS/sUSDS/aUSDC). Tuning window ends 2026-03-14; validation 2026-03-14 → 2026-10-19.
Full report: [docs/reports/backtest-2026-10-20.md](docs/reports/backtest-2026-10-20.md).

| Validation (default premiums A 2 pp / B 6 pp) | All chains | L2 only |
|---|---|---|
| Net yield of the simulated portfolio | 3.36% APY (0.280% / month) | 2.24% APY (0.187% / month) |
| After incident-catalog expected losses | 3.30% APY | 2.20% APY |
| Best whitelisted lending rate (average) | 4.43% | 3.82% |
| Fixed-rate positions opened | 0 | 0 (no risk-allowed candidate on any day) |
| Gas + fees + slippage, share of gross yield | 5.1% | 0.0% |
| Max protocol exposure | 35.1% | 35.3% |

- On every validation day a risk-allowed candidate existed on the all-chain book, but its NLY stayed below the entry threshold:
  by 5.18 pp on the median day and by 1.52 pp on the best day. Over the 11-month tuning window one position was opened.
- The portfolio trails the best single lending rate because the 35% protocol cap spreads capital over lower-paying venues.

| Benchmark target | Threshold | All chains (validation) |
|---|---|---|
| Net yield on deposit (primary) | >= 0.50% / month | 0.276% / month: not met |
| Excess over best lending rate | >= 3 pp APY | -1.07 pp: not met |
| Weeks with >= 70% of capital in fixed-rate positions | >= 70% | 0 of 33: not met |
| Costs share of gross yield | <= 15% | 5.1%: met |
| Exposure caps (protocol / tier B) | 35% / 25% | 35.1% / 0.0%: met |
| Positive after incident losses | > 0 | 3.30% APY: met |

### Live snapshot (2026-10-20)

- 97 active Pendle markets on 13 chains; on whitelisted assets only 2 markets, both on Ethereum: PT-sUSDS and PT-sUSDe
  (36 days to maturity, implied APY 5.1–5.3%, NLY after costs about 2.5% for sUSDS and 2.2% for sUSDe at 3,500 USD).
- Arbitrum had 4 active markets (USDai/sUSDai) and Base 8 (tokenized equities, RWA, points tokens): none on whitelisted assets.
- Lending rates on whitelisted core markets: 2.3–5.7%.
- Fork simulation of both Ethereum markets (1,000 USD): PT received differed from the model by +0.01% and from the Pendle
  quote by less than +0.001%; entry used about 290–300k gas, exit about 265k gas.
- Pendle quotes for 1,000–5,000 USD: price impact about 0.011%, Pendle fee about 0.01% of notional for 36 days.

### Reading the results

Over the period studied, fixed rates on whitelisted tier A/B assets were close to the best lending rates before costs and below
them after costs; higher fixed rates appeared on assets outside the whitelist, i.e. where the market prices extra risk.
The cost model itself is accurate (fork and quote checks agree within hundredths of a percent).

### Limitations

- Historical gas (fixed gwei per chain x ETH price), historical slippage (pool-TVL heuristic) and point-in-time peg/TVL screens are
  modelled, not observed; stablecoins are assumed at peg while held, with incident losses subtracted separately.
- The whitelisted sample is small (24 markets in 18 months), so results are sensitive to individual markets.
- The live window at the time of writing is short; `ballast report` regenerates live statistics as data accumulates.
- Incident base rates use small samples and assumed universes (8 tier A-like, 30 tier B-like assets): order-of-magnitude only.

## Quick start

Requirements: Docker with Compose, [uv](https://docs.astral.sh/uv/), Foundry (Anvil) for local fork runs, a free Alchemy key.
Step-by-step guide (Russian): [docs/guides/user-guide.md](docs/guides/user-guide.md).

```bash
cp .env.example .env          # RPC_URL_* (Alchemy free tier), TELEGRAM_* (optional), DB_PASSWORD
make up                       # postgres, redis, migrations, beat, collector, analytics, simulator, notifier
make ps                       # every service should be healthy
make kick                     # run one scan now (the schedule starts after the first interval)
make backfill                 # load matured-market history (hours; resumable)
make backtest                 # out-of-sample backtest -> docs/reports/backtest-YYYY-MM-DD.md
make simulate                 # fork-simulate current candidates
make report                   # live statistics -> docs/reports/research-report.md
make down                     # stop; collected data stays in the pgdata volume
```

Development (no Docker needed):

```bash
uv sync
make check                    # ruff + mypy --strict + pytest
uv run ballast --help         # scan, backtest, simulate, report, verify-whitelist, notify-test, digest
```

`ballast scan`, `backtest`, `simulate` and `report` also run outside Docker against any PostgreSQL (`DB_URL_OVERRIDE`).

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
- `ballast.sources`, `ballast.chains`: HTTP and JSON-RPC clients with retries, rate limits and secret redaction.
- `ballast.costs`, `ballast.nly`, `ballast.risk`, `ballast.allocation`: pure decision logic shared by live scans, backtest and simulation.
- `ballast.portfolio`: paper portfolio state machine and persistence. `ballast.execution`: transaction guards and the Anvil executor.
- `ballast.pipeline`: composition root, scan runner, quotes, history backfill, fork simulation. `ballast.backtest`, `ballast.analytics`:
  backtest engine, benchmark targets, reports. `ballast.workers`: Celery tasks and schedule.
- Each container gets only the variables it needs: the Telegram token reaches only `notifier`, RPC keys only chain-facing workers.

## Configuration

- `.env` (from `.env.example`): RPC URLs, Telegram, database, allocation limits. Percent values are in percent units (`35` = 35%).
- `config/whitelist.yaml`: protocols, assets, tiers, pinned addresses, lending venues. Unknown assets are rejected.
- `config/incidents.yaml`: sourced incident catalog used by risk scoring and the incident adjustment.
- Parameters and their history: [docs/decisions.md](docs/decisions.md). Data sources and their limits:
  [docs/research/data-sources.md](docs/research/data-sources.md). Incident catalog: [docs/research/incidents.md](docs/research/incidents.md).

## Development

Backlog and done items: [todo.md](todo.md). Changes: [CHANGELOG.md](CHANGELOG.md).
