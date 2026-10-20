# Decisions log

Parameters of the analysis and why they were chosen. Every change gets a dated entry.
Thresholds tuned on a data window must be re-validated on data not used for tuning.

## 2026-10-20: Analysis parameters

### Simulated capital
- Paper portfolio: **10,000 USD** per book (`all` chains and `l2` only).

### Benchmark targets
A strategy is considered worthwhile only if, on data not used to tune thresholds, it meets:
- primary: net portfolio yield (after gas, fees, slippage and incident adjustment) **≥ 0.5% per month** (~6.2% APY);
- net yield ≥ best lending rate **+ 3 pp APY**;
- in ≥ 70% of weeks, ≥ 70% of capital sits in fixed-rate positions;
- gas + fees + slippage ≤ 15% of gross yield;
- fork simulation within **0.3%** of the model;
- no protocol > 35% of capital, tier B ≤ 25%;
- yield positive after incident-catalog losses.

Note: the 70% venue cap on Pendle makes the "70% of capital in fixed-rate positions" target reachable only at the cap itself;
read it together with the share of the maximum allowed exposure.

### Chains
- Analysed in depth: **Ethereum, Arbitrum, Base** (Ethereum added because Arbitrum and Base had no whitelisted Pendle markets;
  see `docs/research/data-sources.md`). Results are reported for "all chains" and "L2 only" separately.
- Gas budget: entry + exit gas ≤ **0.3%** of position size; the minimum position is derived from live gas.
- Recorded for coverage only: the other Pendle chains (1, 10, 56, 143 Monad, 146, 196, 999 HyperEVM, 4663, 5000, 9745 Plasma,
  80094). Monad and Plasma list sUSDe markets on bridged USDe (bridge risk on top of asset risk). Starknet, Polygon, Tron and zkSync
  have no Pendle deployment.

### Assets and limits
| Tier | Assets | Limit |
|---|---|---|
| A | USDC, USDT, DAI, USDS and established yield-bearing wrappers (sUSDS, Aave aTokens) | per protocol ≤ 35% |
| B | Synthetic dollars: USDe, sUSDe | tier B total ≤ 25%, per asset ≤ 15% |
| C | Anything else | rejected |

- Unknown asset or contract: rejected. Token addresses are pinned in `config/whitelist.yaml` and verified on-chain.
- Maximum time to maturity: **183 days**. Reserve: 5% (kept in lending, never in fixed-rate positions).
- The 35% cap applies to the yield-bearing protocol or issuer (Ethena, Sky, Aave, Compound), lending included. Issuers of the
  unit of account (Circle, Tether) are exempt. Pendle as a venue has its own 70% cap.
- Entry threshold = best whitelisted lending rate + tier premium (**A +2 pp, B +6 pp**) + 0.02 pp per risk point.

### Lending ("parking") venues
- Core markets only: DefiLlama pools with `poolMeta` are excluded (Aave *Umbrella* is slashable staking, *Prime*/*Horizon*
  are separate instances, Compound *WETH-pool* is a different market). Minimum pool TVL 20M USD.
- Venues: Aave v3, Compound v3, Sky Savings Rate (USDS → sUSDS, redeemable any time).
- Benchmark lending rate = best whitelisted rate (conservative: raises the bar).
- The paper portfolio stays in a held venue unless another venue of the same protocol pays at least 0.5 pp more.

### Data
- Snapshot interval: **15 minutes**; Pendle quotes hourly; history backfill and fork simulation daily.
- Sources: Pendle public API, DefiLlama (yields, TVL, prices), Alchemy free-tier RPC, public futures APIs (data only).

## 2026-10-20: Cost model calibration (live Pendle quotes, Ethereum sUSDe/sUSDS, 1k and 5k USD)

Quotes: price impact 0.011–0.012%, Pendle fee ~0.01% of notional for 37 days, `gasUsed` 264k–493k.
- Entry gas **450k** units, exit gas **350k** (upper end of quotes plus approval). Fork runs used ~290–300k and ~265k.
- Pendle fee = market `feeRate` x days / 365 (matched the quoted fee); 10 bps only when `feeRate` is missing.
- Swap legs for non-USDC underlyings: 5 bps each; heuristic slippage stays conservative when no quote exists.

## 2026-10-20: Project focus

Research results (README, `docs/reports/backtest-2026-10-20.md`) showed fixed rates on whitelisted assets below the best
lending rate after costs, with the primary yield target not met out-of-sample. The project is maintained as a research and
simulation toolkit: no keys, no transactions outside a local fork. The Docker Compose stack stays for continuous collection.
