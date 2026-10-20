# Decisions log

Agreed parameters. Changing any of them requires the user's approval and a dated entry below.
Thresholds tuned on a data window must be re-validated on data not used for tuning.

## 2026-10-20: Stage 0 answers

### Capital
- Stage 1 paper portfolio: **10,000 USD** virtual.
- Stage 2 allocation: up to **10,000 USD**; the rest of the capital stays in reserve outside the system.
- Stage 2 starts with **500–1,000 USD** and scales only after the user confirms results.

### Yield targets and Go criteria
- Mandatory "plus to deposit" criterion: net portfolio yield (after gas, fees, slippage and risk adjustment) **≥ 0.5% per month**
  (~6.2% APY), measured on data not used to tune thresholds.
- Portfolio net yield must beat the parking rate by **≥ 3 pp APY**.
- In ≥ 70% of weeks the scanner finds enough opportunities to deploy ≥ 70% of capital.
- Gas + slippage ≤ 15% of gross yield.
- Fork simulation matches the model within **0.3%**.
- No protocol > 35% exposure; Tier B total ≤ 25%.
- Yield stays positive after incident-catalog loss adjustment.
- Evaluation window: ≥ 4 weeks of live collection + backtest on matured markets (last 12–18 months).

### Chains
- **Arbitrum, Base.** Ethereum mainnet excluded (gas too heavy for this capital size).
- Gas budget: entry + exit gas ≤ **0.3%** of position size; the minimum position size is derived from it
  (expected ~250 USD on L2; recomputed from live gas).

### Assets and limits
| Tier | Assets | Limit |
|---|---|---|
| A | USDC, USDT, DAI, USDS and their established yield-bearing wrappers (e.g. sUSDS, sDAI, Aave aTokens) | per protocol ≤ 35% |
| B | Synthetic dollars: USDe, sUSDe | Tier B total ≤ 25%, per asset ≤ 15% |
| C | Anything else | **forbidden** |

- Unknown asset or contract: rejected automatically.
- Max time to maturity: **183 days** (~6 months).
- Entry threshold = parking rate + tier premium. Initial premiums (to be calibrated in Stage 1 on a tuning window,
  then validated out-of-sample): **Tier A +2 pp APY, Tier B +6 pp APY**.
- Cash reserve inside the system: 5% (kept in parking, never locked).

### Data
- Snapshot interval: **15 minutes**.
- Sources: Pendle public API, DefiLlama (yields, TVL), Alchemy free tier RPC (Arbitrum, Base), public CEX APIs for dated futures.

### Modules
- Dated futures vs spot on CEX: **data collection only** in Stage 1; trading decided after the Stage 1 report.
- Own executor smart contract: **decide at the start of Stage 2** based on Stage 1 results. Until then, plain transactions with fork simulation.

### Wallets and infrastructure
- No wallets needed in Stage 1. The user will get a hardware wallet before Stage 2; the hot wallet is created in Stage 2.
- RPC: Alchemy free tier. VPS (Stage 2): ~5–7 USD/month (Hetzner CX22 class).
- Dashboard admin credentials: Stage 3, placeholders until then.

## 2026-10-20: Ethereum added to scanning

Data-source research (`docs/research/data-sources.md`) found 0 whitelisted Pendle markets on Arbitrum (4 active, all USDai/sUSDai)
and Base (8 active, exotic: tokenized stocks, RWA, points). Ethereum has 52 active markets incl. sUSDe and sUSDS.
User decision: **add Ethereum** to scanning, paper portfolio and Go criteria. Minimum position per chain is derived from live gas
(entry + exit gas ≤ 0.3% of position). The Go/No-Go report shows results for "L2 only" and "with Ethereum" separately.

### Interpretations (defaults, adjustable)
- The 35% per-protocol cap applies to the *yield-bearing* protocol/issuer (Ethena, Sky, Aave, Circle...), also to parking.
  Pendle as a venue has its own cap `MAX_VENUE_PCT` = 70%; otherwise 70% deployment would be impossible by construction.
- The scanner records **all** stablecoin markets (for the research report); only whitelisted assets can enter the paper portfolio.

## 2026-10-20: Other chains (user asked about Starknet, Polygon, Tron, zkSync)

Pendle API `/v1/chains` lists: 1, 10, 56, 143 (Monad), 146, 196, 999 (HyperEVM), 4663, 5000, 8453, 9745 (Plasma), 42161, 80094.
No Pendle on Starknet, Polygon, Tron or zkSync (non-EVM Starknet/Tron also lack comparable fixed-rate venues): not added.
Notable: Monad sUSDe (~16.5M USD liquidity), Plasma sUSDe (~3.4M). Both are young chains with bridged (OFT) USDe: bridge risk.
Default: **observe all Pendle chains** (Pendle API only, no RPC) for the report; **paper portfolio and Go criteria use
Ethereum, Arbitrum, Base only**. Young chains can be reconsidered at the post-Stage-1 fork with data.
