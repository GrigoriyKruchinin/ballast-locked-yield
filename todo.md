# todo.md: Ballast

Legend: `[x]` done and verified, `[ ]` open (partial work keeps `[ ]` with a status note).
Agreed parameters: [docs/decisions.md](docs/decisions.md).

## Agreed parameters (summary)

- Paper capital 10,000 USD; Stage 2 up to 10,000 USD, start with 500–1,000 USD.
- **Mandatory Go threshold: net yield ≥ 0.5% / month** (~6.2% APY) on out-of-sample data, and ≥ parking + 3 pp APY.
- Chains: Ethereum, Arbitrum, Base (Ethereum added 2026-10-20: no whitelisted Pendle markets on L2s). Gas (entry + exit) ≤ 0.3% of position.
- Tier A: USDC, USDT, DAI, USDS + established wrappers. Tier B: USDe, sUSDe (total ≤ 25%, per asset ≤ 15%). Tier C forbidden.
- Per protocol ≤ 35% (issuer/lending protocols, incl. parking); Pendle venue ≤ 70%. Max maturity 183 days. Reserve 5%. Entry = parking + premium (A +2 pp, B +6 pp, to calibrate).
- Snapshots every 15 min. Fork tolerance 0.3%.

## Stage 0: Infrastructure

- [x] Clarifying questions asked; answers recorded in `docs/decisions.md`
- [x] "Plus to deposit" threshold fixed (≥ 0.5% / month) and asset whitelist agreed
- [x] `CLAUDE.md` (commands, style, workflow, branches, compaction rule, post-Stage-1 fork protocol)
- [x] `.claude/agents/`: implementation-agent, security-risk-critic
- [x] `.claude/skills/`: check-code-quality, run-tests, audit-risks, run-backtest
- [x] `todo.md`
- [x] `.gitignore`, `.env.example`, first commit and push
- [x] `README.md` header and `docs/assets/logo.svg`
- [x] `docs/guides/stage-1-user-guide.md`
- [ ] User completes the Stage 1 guide and says **"Start Implementation"**

## Stage 1: MVP + research (no real money), branch `stage-1-mvp`

### Foundation
- [x] `pyproject.toml` (uv, Python 3.12), ruff, mypy strict, pytest, pytest-asyncio, hypothesis; `uv.lock`
- [x] `Makefile`: `lint`, `typecheck`, `test`, `check`, `up`, `down`, `migrate`, `backtest`, `scan`, `simulate`, `report`
- [x] Package layout (`src/ballast/`: `domain`, `sources`, `chains`, `risk`, `costs`, `allocation`, `execution`, `portfolio`, `notify`, `storage`, `workers`, `analytics`, `config`)
- [x] Modular Pydantic Settings: `DatabaseSettings`, `RedisSettings`, `ChainSettings`, `DataSourceSettings`, `RiskSettings`, `AllocationSettings`, `CostSettings`, `TelegramSettings`, `AppSettings`; per-role settings factories
- [x] Asset/protocol whitelist config (YAML/TOML) with tiers and limits; unknown = rejected; validation tests
- [ ] Pin and on-chain verify remaining whitelist addresses (USDS, sUSDS, aTokens, USDe, sUSDe, USDT on Base) via `make verify-whitelist`
- [x] Domain model: `Token` (address, chain, decimals), `Amount` (Decimal + decimals), `Opportunity`, `Position`, `RiskScore`, `CostEstimate`, `NlyResult`
- [x] Interfaces: `ILockedYieldSource`, `IChainClient`, `IPriceOracle`, `IRiskRule`, `ICostModel`, `IAllocator`, `ITxExecutor`
- [x] Structured logging with secret redaction (test that keys never appear)

### Storage
- [ ] SQLAlchemy async models: market snapshots, rate snapshots, futures snapshots, NLY results, risk scores, paper positions, simulated txs, monitor events, decision log (entered/skipped + reason), backtest runs/results
- [ ] Alembic setup + initial migration; migrations run in a dedicated container
- [ ] Repository layer + tests (testcontainers or dockerized Postgres)

### Data Collector
- [x] Verify current docs and rate limits: Pendle API, DefiLlama, Alchemy, CEX futures APIs (subagent; summary to `docs/research/data-sources.md`)
- [ ] Pendle source: markets on Ethereum/Arbitrum/Base (PT price, implied APY, maturity, liquidity, underlying, SY/PT/YT addresses)
- [ ] Parking rates: Aave v3 (and similar Tier A lending) via DefiLlama + RPC cross-check
- [ ] Dated futures vs spot: public prices (Binance, OKX, Deribit) → annualized basis (data only)
- [ ] Gas price collector per chain (L2 execution + L1 data fee)
- [ ] Celery beat schedule: snapshots every 15 min; retries, backoff, rate-limit handling
- [ ] Opportunity Normalizer: all sources → `Opportunity`

### Backtest on matured markets
- [ ] Discover Pendle markets matured in the last 12–18 months on allowed chains (include failed/delisted: no survivorship bias)
- [ ] Reconstruct historical PT price / implied APY, liquidity and slippage; if history is not available via API, record that explicitly
- [ ] Historical gas cost model
- [ ] Backtest engine using the same Allocator + `ITxExecutor` (historical simulator)
- [ ] Tuning / validation time split; thresholds chosen only on tuning data

### NLY Calculator and Cost Model
- [x] `ICostModel`: entry/exit gas, pool/protocol fees, slippage (liquidity heuristic + quoted-impact model)
- [ ] Feed live Pendle price-impact quotes into `QuotedSlippage`; calibrate heuristic vs quotes and fork runs
- [x] NLY = (payoff at maturity − entry cost − gas in/out − slippage − fees) / capital / term → APY and % per month
- [ ] Min position size such that gas ≤ 0.3% (done: `min_position_usd`); max position size from liquidity impact (in Allocator)
- [x] Unit + hypothesis property tests (monotonicity, decimals round-trip, conservative rounding)

### Risk Scorer and incident catalog
- [x] `IRiskRule` set: age & audits, TVL level and trend, par redemption path, depeg history, oracle dependency, centralized counterparties, pause/admin keys
- [x] Risk score per asset/protocol; tier assignment; entry threshold = parking + tier premium (+ 0.02 pp per risk point)
- [ ] Incident catalog (`data/incidents.yaml` + `docs/research/incidents.md`) with sources: synthetic stablecoin depegs and collapses, protocol hacks, freezes
- [ ] Per tier: premium over parking vs historical incident losses (does the risk pay?)

### Allocator
- [ ] Limits: per protocol 35%, Tier B 25%, per B asset 15%, max maturity 183 days, reserve 5%, min position
- [ ] Ranking by risk-adjusted NLY; liquidity-capped sizing; fallback to parking
- [ ] Decision log for every entered/skipped opportunity
- [ ] Tests for every limit and edge case

### Fork Simulator (Anvil)
- [ ] `ITxExecutor` fork implementation: spawn Anvil fork (Arbitrum/Base), fund test account, run entry/exit via Pendle router
- [ ] Exact approvals, `minOut`, whitelist and recipient checks shared with future live executor
- [ ] Compare simulated result vs model; tolerance 0.3%; store results

### Paper Portfolio
- [ ] Virtual 10,000 USD portfolio on live data incl. gas and slippage
- [ ] Daily snapshot: positions, accrued yield, parking balance, upcoming maturities
- [ ] Maturity handling: redeem at par → back to parking → reallocate

### Monitoring (research mode)
- [ ] Monitors: peg deviation, TVL drop, contract pause, rate spike, oracle status → events in DB + alerts

### Telegram
- [ ] Notifier (HTML), emojis, bold, separators, plain-language explanations
- [ ] Alerts: new opportunities above threshold, daily and weekly summaries, risk events

### Docker (local)
- [ ] Dockerfiles; `docker-compose.local.yml`: postgres, redis, migrations, beat, collector worker, analytics worker, telegram worker, fork simulator
- [ ] Healthchecks for all services; `depends_on: condition: service_healthy`; per-role env subsets
- [ ] `.github/workflows/ci.yml`: ruff, mypy, pytest, `docker compose config`

### Research and report
- [ ] ≥ 4 weeks of live collection
- [ ] Run `run-backtest` (out-of-sample) and `audit-risks`
- [ ] `docs/reports/stage-1-go-no-go.md`: metrics, NLY distribution, weekly opportunities, parking comparison, fork checks, incident catalog, risk assessment, recommendation, "Простыми словами" block
- [ ] Go/No-Go criteria evaluated:
  - [ ] Net yield ≥ 0.5% / month on out-of-sample data (mandatory)
  - [ ] Portfolio ≥ parking + 3 pp APY
  - [ ] ≥ 70% of weeks with ≥ 70% capital deployable
  - [ ] Gas + slippage ≤ 15% of gross yield
  - [ ] Fork simulation within 0.3%
  - [ ] No protocol > 35%, Tier B ≤ 25%
  - [ ] Positive after incident-loss adjustment
- [ ] README updated (features, quick start, architecture, configuration); status badge
- [ ] `chore: complete stage 1`, tag `v0.1.0`, merge to `main`, push

## Fork after Stage 1

- [ ] Discuss the Go/No-Go report with the user (plain Russian, no fitting)
- [ ] Outcome chosen: Go / Rework (≤ 2 iterations, re-validated on new data) / No-Go → research toolkit (protocol in `CLAUDE.md`, tag `v1.0.0`)
- [ ] If Go: `docs/guides/stage-2-user-guide.md`, wait for user confirmation

## Stage 2: Production (only on Go), branch `stage-2-prod`

- [ ] Decide on own executor contract (default: plain transactions + fork simulation)
- [ ] Live `ITxExecutor`: build tx, fork-simulate, exact approve, `minOut`, gas control, retries, confirmations
- [ ] Position Manager state machine: created → entered → holding → matured/exited → returned to parking; failed tx, stuck nonce, retries
- [ ] Startup reconciliation with chain state (no duplicates after restart)
- [ ] Maturity Scheduler: automatic redemption and reallocation
- [ ] Risk Manager: limits, reserve, daily outgoing tx limit, anomaly lock (state mismatch, RPC down, suspicious prices)
- [ ] Kill-switch: Telegram command + automatic; emergency exit to parking with precomputed exit losses
- [ ] (Optional) executor contract: owner-only withdrawals, target whitelist, per-tx limit, pause; Foundry fuzz + invariant tests, Slither, testnet + fork first
- [ ] (Optional) CEX dated futures vs spot module: no-withdrawal keys, margin control
- [ ] Post-trade check: actual gas / slippage / price vs model, warn above tolerance
- [ ] Prod `docker-compose.yml`; hot-wallet key visible only to the executor container
- [ ] CI: ruff, mypy, pytest, `docker compose config`, image builds (+ `forge test`, Slither); green CI required for `main`
- [ ] Image publishing to GHCR (version + SHA tags)
- [ ] CD `deploy.yml`: SSH deploy, pull, Alembic migration container, `up -d`, healthcheck verification
- [ ] Auto-rollback on failed healthchecks + Telegram alert; manual rollback via `workflow_dispatch`
- [ ] Telegram deploy notifications; `production` environment with required reviewer
- [ ] First successful automatic deploy to VPS
- [ ] Telegram: entries/exits, warnings, daily digest, monthly report on the 1st
- [ ] Start with 500–1,000 USD; scale after user confirmation
- [ ] `docs/guides/stage-3-user-guide.md`; `chore: complete stage 2`, tag `v0.2.0`

## Stage 3: Growth and analytics (only on Go), branch `stage-3-dashboard`

- [ ] FastAPI admin panel, Basic Auth/JWT, owner-only
- [ ] Analytics API
- [ ] Chart.js UI: top income (day/week/month/all time), top positions, top sources/protocols/assets
- [ ] Charts: cumulative income vs parking, deployed vs idle capital, maturity ladder timeline, gas and slippage
- [ ] Tables: open positions, recent txs, monitor events, skipped opportunities with reasons
- [ ] Model vs reality per position
- [ ] Risk map: exposure by protocol/asset/tier; system health (uptime, data lag, RPC errors)
- [ ] Plugin sources and chains; multi-exchange basis module; automatic threshold/whitelist review
- [ ] Backups, profiling, monitoring (Healthchecks/Sentry/Prometheus as needed)
- [ ] `chore: complete stage 3`, tag `v0.3.0`
