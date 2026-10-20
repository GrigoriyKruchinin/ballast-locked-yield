# Incident catalog: methodology and base rates

Machine-readable catalog: [`config/incidents.yaml`](../../config/incidents.yaml) (31 entries, 2022-01-01 to 2026-10-20).
All figures are **estimated/historical**, compiled from secondary reporting (news outlets, protocol forums,
post-mortems). Where sources disagree, the YAML `notes` field gives the range.

## Methodology

**What counts.** An event is included if it caused a reported loss of peg, frozen funds, bad debt or a forced
change of redemption terms for:

- a dollar stablecoin or synthetic/yield-bearing dollar (depegs, collapses);
- a lending or fixed-yield venue, or a direct integration of one, that Ballast uses or that is comparable
  (hacks, oracle failures, governance capture);
- a major issuer's address-level freeze or regulatory wind-down (freezes, governance).

Venue-specific prints (e.g. USDe on Binance) are included but flagged, because they matter for anything that
uses that venue's price as an oracle even when on-chain redemption held. The catalog favours events relevant to
Ballast's assets and venues; it is **not** an exhaustive list of every small stablecoin failure (one monitoring
vendor counts 36 stablecoin collapses with measurable loss since 2022).

**kind.** `depeg` (traded below peg, backing broadly intact), `collapse` (backing impaired or the issuer ceased
redemptions), `hack` (exploit or key compromise), `freeze` (issuer blacklist), `oracle` (mispricing without
exploit), `governance` (rule changes or capture).

**tier_like.** Mirrors `config/whitelist.yaml`: **A** = fiat-backed or major stablecoin, or a lending receipt of
one on a major venue (aUSDC-like, also used for incidents on the parking venues themselves); **B** = synthetic or
yield-bearing dollar, including curated yield vault shares; **C** = other (algorithmic with endogenous
collateral, non-dollar assets, third-party wrappers, small venues). Tier C is never allowed by the whitelist and
is listed for context only.

**min_price_usd.** Worst widely reported traded price. Different venues and data providers report different
lows, so this is an order-of-magnitude indicator, not an exact tick.

**holder_loss_pct.** Permanent loss, in percent of par, for someone who **held through to resolution** (redeemed
at the end, received the recovery distribution, or still holds at the latest known mark). It is **not** the loss
of someone who panic-sold at the bottom, which is usually much larger (e.g. USDC at $0.877 is a 12.3% loss for a
seller but 0% for a holder). For unresolved events it is a mark-to-market or recovery estimate with a range in
`notes`. For venue hacks it refers to depositors in the affected markets (e.g. 0% for Pendle PT holders in the
Penpie exploit, ~1% for Aave WETH suppliers in the Kelp exploit, ~0% for USDC suppliers).

## Incidents

| Date | ID | Kind | Assets | Min price | Holder loss % | Source |
|---|---|---|---|---|---|---|
| 2022-05-09 | ust-terra-2022-05 | collapse | UST | 0.01 | 98 | [CoinDesk](https://coindesk.com/learn/the-fall-of-terra-a-timeline-of-the-meteoric-rise-and-crash-of-ust-and-luna) |
| 2022-05-12 | usdt-terra-contagion-2022-05 | depeg | USDT | 0.95 | 0 | [CNBC](https://www.cnbc.com/2022/05/12/tether-usdt-stablecoin-drops-below-1-peg.html) |
| 2022-06-13 | usdd-2022-06 | depeg | USDD | 0.91 | 0 | [Fortune/CoinDesk](https://fortune.com/crypto/2022/06/13/algorithmic-stablecoin-usdd-loses-peg-justin-sun-tron-decentralized-usd) |
| 2022-08-08 | usdc-tornado-freeze-2022-08 | freeze | USDC | – | 0 | [The Block](https://www.theblock.co/post/162172/circle-freezes-usdc-funds-in-tornado-cashs-us-treasury-sanctioned-wallets) |
| 2023-02-13 | busd-paxos-2023-02 | governance | BUSD | – | 0 | [NYDFS](https://www.dfs.ny.gov/consumers/alerts/Paxos_and_Binance) |
| 2023-03-11 | usdc-svb-2023-03 | depeg | USDC | 0.877 | 0 | [The Block](https://www.theblock.co/post/218993/usdc-and-dai-remain-at-about-0-90-following-circles-disclosure-of-funds-at-svb) |
| 2023-03-11 | dai-svb-2023-03 | depeg | DAI | 0.897 | 0 | [The Block](https://www.theblock.co/post/218993/usdc-and-dai-remain-at-about-0-90-following-circles-disclosure-of-funds-at-svb) |
| 2023-03-11 | frax-svb-2023-03 | depeg | FRAX | 0.885 | 0 | [HackerNoon](https://hackernoon.com/usdc-got-depegged-following-the-svb-collapse) |
| 2023-03-13 | euler-v1-hack-2023-03 | hack | USDC, DAI, WETH, WBTC, stETH | – | 1 | [rekt.news](https://rekt.news/euler-rekt/) |
| 2023-07-15 | gho-below-peg-2023-07 | depeg | GHO | 0.94 | 0 | [The Defiant](https://thedefiant.io/news/defi/aave-s-gho-stablecoin-consistently-trades-under-dollar-peg) |
| 2023-07-30 | curve-vyper-2023-07 | hack | alETH, msETH, pETH, CRV | – | 27 | [Halborn](https://www.halborn.com/blog/post/explained-the-vyper-bug-hack-july-2023) |
| 2023-10-11 | usdr-tangible-2023-10 | collapse | USDR | 0.50 | 40 | [The Block](https://www.theblock.co/post/255946/real-usd-usdr-stablecoin-depegs-and-price-crashes-by-50) |
| 2023-11-20 | tether-freeze-2023-11 | freeze | USDT | – | 0 | [The Block](https://www.theblock.co/post/263802/tether-freezes-225-million-worth-of-stolen-usdt-after-doj-investigation) |
| 2024-07-28 | compound-humpy-governance-2024-07 | governance | COMP | – | 0 | [The Block](https://theblock.co/news/ecosystems/2024-07-28-24-million-compound-finance-proposal-passed-by-whale-over-dao-objections-307943) |
| 2024-09-03 | penpie-2024-09 | hack | wstETH, sUSDe, rswETH | – | 0 (PT holders) | [rekt.news](https://rekt.news/penpie-rekt) |
| 2025-01-10 | usd0pp-usual-2025-01 | governance | USD0++ | 0.89 | 2 | [The Block](https://www.theblock.co/post/333995/usual-money-protocol-update) |
| 2025-02-21 | ethena-bybit-2025-02 | hack | USDe | – | 0 | [Chaos Labs](https://governance.aave.com/t/chaos-labs-update-on-bybit-security-event-and-usde-market-reaction/21158) |
| 2025-04-02 | fdusd-2025-04 | depeg | FDUSD | 0.87 | 0 | [Cointelegraph](https://cointelegraph.com/news/first-digital-redeems-26m-fdusd-after-stablecoin-depeg) |
| 2025-04-10 | susd-synthetix-2025-04 | depeg | sUSD | 0.66 | 20 | [Decrypt](https://decrypt.co/315338/synthetixs-susd-stablecoin-depegs-to-new-low-of-0-66) |
| 2025-06-26 | resupply-2025-06 | hack | reUSD | – | 0 | [Forklog](https://forklog.com/en/resupply-protocol-hacked-for-9-5-million/) |
| 2025-10-10 | usde-binance-2025-10 | depeg | USDe | 0.65 (Binance only) | 0 | [The Block](https://theblock.co/post/374295/binance-pays-283-million-in-compensation-following-fridays-depegs-covering-user-losses) |
| 2025-11-03 | balancer-v2-2025-11 | hack | osETH, wstETH, BPT | – | 75 | [Verichains](https://blog.verichains.io/p/balancer-exploit-small-rounding-errors) |
| 2025-11-04 | stream-xusd-2025-11 | collapse | xUSD | 0.24 | 80 | [The Block](https://www.theblock.co/post/377400/stream-finance-halts-withdrawals-93-million-loss) |
| 2025-11-04 | curated-vault-stream-contagion-2025-11 | collapse | USDC (curated vaults) | – | 3.6 | [Coinfomania](https://coinfomania.com/morpho-delists-elixir-sdeusd-bad-debt/) |
| 2025-11-06 | elixir-deusd-2025-11 | collapse | deUSD, sdeUSD | 0.015 | 0 (non-Stream holders) | [The Block](https://www.theblock.co/post/377961/elixir-sunsets-deusd-synthetic-stablecoin-following-stream-finance-unwinding-aims-full-redemptions) |
| 2025-11-06 | usdx-stables-labs-2025-11 | collapse | USDX | 0.30 | 70 | [The Block](https://www.theblock.co/post/377917/synthetic-stablecoin-usdx-depegs-pancakeswap-lista) |
| 2026-03-10 | aave-capo-wsteth-2026-03 | oracle | wstETH | – | 0 | [Aave post-mortem](https://governance.aave.com/t/post-mortem-exchange-rate-misallignment-on-wsteth-core-and-prime-instances/24269) |
| 2026-03-22 | resolv-usr-2026-03 | hack | USR, RLP | 0.025 (Curve) | 15 | [The Block](https://www.theblock.co/post/394582/resolvs-usr-stablecoin-depegs-after-attacker-mints-80-million-unbacked-tokens-extracts-roughly-25-million) |
| 2026-04-18 | kelp-rseth-aave-2026-04 | hack | rsETH, WETH | – | 1 (WETH suppliers) | [The Defiant](https://thedefiant.io/news/hacks/aave-models-usd124m-to-usd230m-in-bad-debt-from-kelp-exploit-layerzero-and-kelp-continue-to) |
| 2026-06-03 | apxusd-2026-06 | depeg | apxUSD | 0.90 | 4 | [The Defiant](https://thedefiant.io/apxusd-loses-dollar-peg-bitcoin-slide-strc-backed-collateral) |
| 2026-09-22 | usde-binance-2026-09 | depeg | USDe | 0.92 (Binance only) | 0 | [KuCoin blog](https://www.kucoin.com/blog/usde-depeg-binance-0-92-1011-contagion-risk) (low confidence) |

## Base rates per tier

Observation window: 2022-01-01 to 2026-10-20 = **4.80 years**.

### Incident counts per tier per year

| tier_like | 2022 | 2023 | 2024 | 2025 | 2026 (to Oct 20) | Total | Per year |
|---|---|---|---|---|---|---|---|
| A | 2 | 5 | 0 | 1 | 2 | 10 | 2.1 |
| B | 0 | 3 | 0 | 9 | 3 | 15 | 3.1 |
| C | 2 | 1 | 2 | 1 | 0 | 6 | 1.3 |

Sum of `holder_loss_pct` per tier: A = 2.0, B = 234.6, C = 200.0 (percentage points across all incidents).

### Estimated annual expected loss

The catalog lists incidents, not positions, so expected loss needs an assumption about how many comparable
assets ("universe size" N) the incidents are spread across. Expected annual loss for one position:

    EL_tier = (sum of holder_loss_pct in tier) / (years × N_tier)

**Tier A-like** (USDC, USDT, DAI/USDS, sUSDS, aUSDC/aUSDT on major venues).

- Upper bound (every incident hits your one position, N = 1): 2.0 / 4.80 = **0.42 %/yr**.
- Assumption N_A ≈ 8 (USDC, USDT, DAI/USDS, BUSD, FDUSD, TUSD/PYUSD-class issuers plus Aave/Compound/Euler
  lending receipts as separate exposures): 2.0 / (4.80 × 8) = **0.05 %/yr**.
- Both nonzero contributions are venue events (Euler v1 2023, ~1% after fund return; Kelp/Aave 2026, ~1% on
  **WETH**, not USDC). No Tier A stablecoin holder who held to resolution lost money in the window; all A-tier
  depegs (USDT 0.95, USDC 0.877, DAI 0.897, FDUSD 0.87) fully recovered, though USDC's recovery relied on a US
  government backstop of SVB deposits.
- **Working estimate: 0.05-0.4 %/yr, central ~0.1 %/yr.** The main A-tier risk is a temporary price or liquidity
  shock (forced sale, oracle-driven liquidation, withdrawals blocked at 100% utilisation) rather than
  permanent loss.

**Tier B-like** (synthetic / yield-bearing dollars: USDe, sUSDe and comparable designs).

- Upper bound (N = 1): 234.6 / 4.80 = 48.9 %/yr. This is meaningless as a per-position number; it would be true
  only if one held every failed coin at once.
- Assumption N_B ≈ 30 notable synthetic/yield-bearing dollars live over the window (range 20-40):
  234.6 / (4.80 × 30) = **1.6 %/yr** (N = 20: 2.4 %/yr; N = 40: 1.2 %/yr).
- Cross-check by frequency × severity: 5 events with ≥10% holder loss (USDR, sUSD, xUSD, USDX, USR) in
  4.80 years across ~30 coins gives p ≈ 5 / (4.80 × 30) ≈ 3.5 %/yr per coin; mean loss given such an event
  ≈ (40 + 20 + 80 + 70 + 15) / 5 = 45 %; EL ≈ 0.035 × 45 ≈ **1.6 %/yr**. Consistent with the above.
- Ethena-specific: 3 incidents (Bybit hack 2025, Binance $0.65 print 2025, Binance $0.92 print 2026), 0%
  holder loss in all. That is evidence of robust redemption so far, not of zero risk: n = 3 is far too small
  to rule out a tail event.
- **Working estimate: 1-2.5 %/yr, central ~1.6 %/yr.** Practical implication: a Tier B fixed rate must exceed
  the Tier A parking rate by more than this premium (plus a margin for estimation error) before entry makes
  sense.

**Tier C** is not allowed; for reference its sum (200.0) is dominated by UST (98) and venue hacks (Balancer 75,
Curve 27).

### Caveats (small-sample uncertainty)

- 10 (A) and 15 (B) incidents over under five years: a single new event can move the estimate by a large
  fraction. Treat the numbers as order-of-magnitude priors, not measurements.
- **Selection bias, two directions.** The catalog undercounts small B-like failures (a vendor counts 36
  collapses with measurable loss since 2022, many not listed here), which biases EL_B **down**. The universe size
  N is guessed and failed coins are easier to find than survivors, which can bias it either way.
- **Correlation.** Incidents cluster (SVB weekend: USDC, DAI, FRAX together; November 2025: xUSD, deUSD,
  USDX, curated vaults together). Losses are not independent across positions, so diversification within a tier
  helps less than the per-position EL suggests.
- **Unresolved events.** xUSD, USDX, sUSD, USR, apxUSD and Kelp/Aave had no confirmed final settlement in the
  sources found; their `holder_loss_pct` values are estimates with ranges in the YAML notes.
- **Source quality.** Most figures come from news outlets rather than primary post-mortems; reported lows differ
  by venue and data provider. The 2026-09-22 USDe entry has a single, weak source.
