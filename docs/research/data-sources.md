# Data sources for Ballast (Arbitrum 42161, Base 8453)

Verified 2026-10-20 against current docs (Pendle OpenAPI spec served at
`https://api-v2.pendle.finance/core/docs/swagger.json`, Alchemy pricing/support pages) and live
requests from the dev machine. All examples below are real responses, trimmed. Raw samples were
kept outside the repo (session scratchpad) and are not committed.

Conventions used below: "fraction" = 0.05 means 5 %; "percent" = 5.0 means 5 %.

---

## 1. Pendle API (core)

- **Base URL:** `https://api-v2.pendle.finance/core`
- **Auth:** none on the free tier.
- **Rate limit:** you get a budget of Computing Units (CU). Each response sends these headers:
  `x-computing-unit` (what the call cost), `x-ratelimit-limit: 200` / `x-ratelimit-remaining` /
  `x-ratelimit-reset` (unix time of the next minute boundary), and `x-ratelimit-weekly-limit: 200000` /
  `-remaining` / `-reset`. So the observed free tier is **200 CU/min and 200 000 CU/week**.
  A third-party mirror says 100 CU/min, so read the headers instead of hardcoding a number.
  Going over the limit returns HTTP 429; back off with jitter.
- **Units (from the spec):** every APY, APR and ROI value is a **fraction**. Fields named `ln*` are natural
  logs. Timestamps are ISO-8601 UTC strings, except OHLCV, which uses unix seconds.
- **Asset ID format:** `"{chainId}-{address}"`, e.g. `42161-0xe9d0...`. Market objects use this format for
  `pt`, `yt`, `sy` and `underlyingAsset`, but `address` is the bare address.
- **Freshness:** prices are recomputed about every 15 s to 1 min. Only `swapping-prices` is per block.
  Historical data is aggregated to **hour/day/week only** (no minute bars).

### 1.1 Market list (active and inactive): `GET /v2/markets/all`

Query params: `chainId`, `isActive` (true/false), `ids` (comma-separated `chainId-address`, max 20),
`skip` (default 0), `limit` (default 20, **max 100**), `order_by` (e.g. `name:1`).
Pagination: `{total, limit, skip, results[]}`. Cost: 2 CU.

```json
{"total":4,"limit":100,"skip":0,"results":[{
 "name":"sUSDai","protocol":"USD.AI","chainId":42161,
 "address":"0x37fcd69d332a92a967194016b57ef4ff42bb4607",
 "expiry":"2027-06-24T00:00:00.000Z",
 "pt":"42161-0x9f3c962c7ab12cc823164e2b87e8eeddbf7400c6",
 "yt":"42161-0xf2578bc76de874b054c55844d5f4079f4b095476",
 "sy":"42161-0x30ccf4bbee313fcd19f3e295b3ba2920a24e2f62",
 "underlyingAsset":"42161-0x0b2b2b2076d95dda7817e785989fe353fe955ef9",
 "accountingAsset":"42161-0x0a1a1a107e45b7ced86833863f482bc5f4ed82ef",
 "inputTokens":["42161-0x0a1a...","42161-0x0b2b...","42161-0x4685..."],
 "outputTokens":["42161-0x0b2b..."],
 "details":{"liquidity":9295.81,"totalTvl":57206.50,"tradingVolume":376.07,
   "underlyingApy":0.0557,"impliedApy":0.06915,"swapFeeApy":0.0336,"pendleApy":0.2198,
   "ytFloatingApy":-0.279,"feeRate":0.00136,"aggregatedApy":0.3126,"maxBoostedApy":0.642,
   "totalPt":2495.12,"totalSy":6171.57,"totalSupply":4482.77,"ptRoi":0.0462,"ytRoi":-0.198},
 "isNew":true,"isPrime":false,"isVolatile":false,"timestamp":"2026-10-14T14:00:36.000Z",
 "categoryIds":["stables","rwa","points"],"points":[{"key":"USD.AI","type":"multiplier","value":12}],
 "ytApyBreakdown":{...},"lpApyBreakdown":{...},"limitOrderIncentive":{...},"pendleEmission":{...}}]}
```

- `details.liquidity` = USD value inside the AMM pool. `details.totalTvl` also counts PT held outside the pool.
- `totalPt` / `totalSy` are pool reserves in token units (floats, already scaled by decimals).
- `timestamp` is the market **creation** time, not the time of the snapshot.
- `accountingAsset` is the unit PT redeems to in value terms (here USDai). `underlyingAsset` is the yield token (sUSDai).
- This endpoint does **not** return a PT price. Get it from `/v1/prices/assets` (USD) or `swapping-prices` (in underlying units).
- The legacy `GET /v1/{chainId}/markets/active` and `/inactive` still respond, but they are no longer in the spec and return
  fewer fields (no `totalPt`/`totalSy`/`totalTvl`). Do not use them.
- **The universe is small right now.** Active whitelisted markets: **Arbitrum 4** (USDai/sUSDai, Feb and Jun 2027),
  **Base 8** (apxUSD, yUTY, hBTC, sKAITO, GOOGLc, NVDAc, superNVDA, superTSLA). All chains: 97.
  Inactive (matured): Arbitrum 90, Base 34. Pool liquidity on Arbitrum/Base is mostly $4k–$7.5M.

### 1.2 Asset metadata: `GET /v1/assets/all`

Params: `chainId`, `ids` (max 20), `type` (`PT`,`YT`,`SY`,`PENDLE_LP`, comma-separated), `skip`, `limit` (leave blank for all).
Returns `{"assets":[...]}`:

```json
{"name":"PT HLP (HLP)","decimals":18,"address":"0x0486...cfe0","symbol":"PT-HLP-26DEC2024",
 "tags":["PT"],"expiry":"2024-12-26T00:00:00.000Z","proIcon":"https://...","chainId":42161}
```

This endpoint does not cover non-Pendle tokens (USDC and similar). For those, read `decimals()` on-chain or use DefiLlama coins.

### 1.3 Current USD prices: `GET /v1/prices/assets`

Params: `chainId`, `ids` (max 20), `type`, `skip`, `limit`. Covers PT/YT/SY/LP **and** the underlying assets.

```json
{"prices":{"42161-0xe9d0...1fce":0.97118,"42161-0x0b2b...5ef9":1.11981,"42161-0x0a1a...82ef":0.99994},"total":3,"skip":0}
```

PT price in accounting-asset terms = `price(PT) / price(accountingAsset)`, here 0.97118 / 0.99994 = 0.9712 USDai per PT.

### 1.4 Real-time pre-trade price: `GET /v1/sdk/{chainId}/markets/{market}/swapping-prices`

```json
{"underlyingToken":"0x0b2b...5ef9","underlyingTokenToPtRate":1.15222,"ptToUnderlyingTokenRate":0.86674,
 "underlyingTokenToYtRate":38.137,"ytToUnderlyingTokenRate":0.02514,"impliedApy":0.08719}
```

- Rates are per 1 whole token and are quoted against `underlyingToken` (here **sUSDai**, not USDai).
  So PT in USD = 0.86674 × 1.1198 = 0.9706.
- Rates are `null` when there is not enough liquidity. An expired market returns **404** `{"message":"Given market is expired"}`.

### 1.5 Historical market time series: `GET /v3/{chainId}/markets/{address}/historical-data`

Params: `time_frame` = `hour` | `day` | `week` (default hour), `timestamp_start` and `timestamp_end` (ISO),
`fields` (comma-separated, or `all`; default `underlyingApy,impliedApy,maxApy,baseApy,tvl`),
`includeFeeBreakdown` (doubles the CU cost), `includeApyBreakdown`.
Each call returns **at most 1440 points**, so hourly data covers about 60 days per call: page by time window.
Cost = `ceil(points/300)` CU.

Available fields: `timestamp, impliedApy, ptPrice (USD), ytPrice, syPrice, lpPrice, tvl (pool USD), totalTvl,
totalPt, totalSy, totalSupply, underlyingApy, underlyingInterestApy, underlyingRewardApy, baseApy, maxApy,
pendleApy, swapFeeApy, lpRewardApy, voterApr, ytFloatingApy, tradingVolume (24h USD), lastEpochVotes, aggregatedApy`.

```json
{"total":138,"timestamp_start":"2026-01-14T07:00:00.000Z","timestamp_end":"2026-01-20T00:00:00.000Z",
 "results":[{"timestamp":"2026-01-14T07:00:00.000Z","impliedApy":0.0899999,"tvl":49531.21,
   "totalPt":12275.339059,"totalSy":29758.469841,"ptPrice":0.96232}, ...]}
```

- **Matured markets are covered.** Arbitrum gUSDC 25JUN2026 returns daily data from creation to expiry
  (162 points). wstETH 27JUN2024 returns weekly data back to 2023-06.
- **Gotchas:**
  - `ptPrice` is in **USD**, not underlying. For ETH markets it is around 1800. Divide by the accounting asset's USD price yourself.
  - Implied APY is noisy close to expiry (gUSDC showed 8 % on its last day with PT at 0.9997). Drop the last ~3–7 days before backtesting.
  - The first points of a market have tiny TVL (wstETH started at $2) and should be filtered out.
  - `totalPt` / `totalSy` give a **pool depth proxy** per hour, so slippage can be re-simulated from AMM math. No order-book or limit-order depth history exists.

### 1.6 OHLCV for PT/YT/LP: `GET /v4/{chainId}/prices/{address}/ohlcv`

Params: `time_frame` hour|day|week, `timestamp_start`, `timestamp_end`. Max 1440 points; cost `ceil(n/300)`.
SY and non-Pendle tokens are **not** supported. The body is CSV inside JSON, with unix-second times and USD prices:

```json
{"total":163,"currency":"USD","timeFrame":"day","timestamp_start":1768262400,"timestamp_end":1782259200,
 "results":"time,open,high,low,close,volume\n1768262400,0.9622,0.9622,0.9622,0.9622,0.0000\n..."}
```

Prices are rounded to 4 decimals, which is too coarse for yield math. Prefer `historical-data.ptPrice`.

### 1.7 Quote / slippage / calldata: `POST /v3/sdk/{chainId}/convert`

Body (`ConvertV3Dto`): `receiver` (required), `slippage` (required, fraction), `inputs` (required, `[{token, amount}]`,
amount in **wei string**), `outputs` (required, `[address]`), `enableAggregator` (default false), `aggregators`,
`useLimitOrder` (default **true**), `additionalData` (`"impliedApy,effectiveApy"`), `redeemRewards`, `needScale`.
The action (buy PT, sell PT, roll, redeem after expiry, ...) is inferred from the tokens. Cost: **5 CU**, plus extra CU per aggregator.
Prefer it over the v2 GET variant.

```json
{"action":"swap",
 "requiredApprovals":[{"token":"0x0a1a...82ef","amount":"1000000000000000000000"}],
 "routes":[{"contractParamInfo":{"method":"swapExactTokenForPt","contractCallParamsName":["receiver","market"],...},
   "tx":{"to":"0x888888888889758F76e7103c6CbF23ABbF58F946","from":"0x...dead","data":"0xc81f847a..."},
   "outputs":[{"token":"0xe9d0...1fce","amount":"1029306202055237284472"}],
   "data":{"aggregatorType":"VOID","priceImpact":-0.000297,
     "priceImpactBreakDown":{"internalPriceImpact":-0.000297,"externalPriceImpact":0},
     "impliedApy":{"before":0.0871896,"after":0.0871896},"effectiveApy":0.08627,
     "fee":{"usd":0.622},"intermediateSyAmount":"892956675662450723765","gasUsed":"943846"}}]}
```

- The response **includes calldata** (`tx.to`, `tx.data`), which can be replayed on an Anvil fork. `gasUsed` is the API's own estimate.
- `priceImpact` is a fraction and negative means worse. `effectiveApy` is the APY after impact and fees, which is the number NLY needs.
- **Gotchas:**
  - With `useLimitOrder=true`, limit orders fill first. A 1k and a 100k quote on the same market showed the same
    price impact (−0.03 %) because a large limit order absorbed both. For an AMM-only estimate, also quote with `useLimitOrder:false`.
  - The spec says not to use SDK endpoints for price polling (CU cost). Call `convert` only for candidate sizes, not for every market every tick.
  - It can be called for any `receiver` with no balance check, so `0x...dEaD` works for quotes. Fork simulation still needs real approvals.

### 1.8 Pendle Router V4

`0x888888888889758F76e7103c6CbF23ABbF58F946`. It is the same on **Arbitrum and Base**: a small proxy
(287 bytes of code on both chains, verified with `cast codesize`). This is also the `tx.to` the convert API returns. Whitelist it per chain and
check that `tx.to` in every convert response equals it.

Other endpoints that may help: `/v1/{chainId}/assets/{address}/oracle-apy-history` (history of the underlying APY),
`/v1/statistics/market-fees`, `/v5/{chainId}/transactions/{address}` (trades), `/v1/chains` (`[1,56,143,196,999,4663,8453,9745,42161,10,146,5000,80094]`).

---

## 2. DefiLlama

Auth: none. Rate limit: not documented for the free tier (third parties say about 300–500 req/5 min). Cache responses.
`yields.llama.fi` works for free, verified live.

### 2.1 `GET https://yields.llama.fi/pools`

About 11 MB and 16 107 pools in a single response, with no pagination or filters. Fetch at most every 15–60 min.

```json
{"status":"success","data":[{"chain":"Arbitrum","project":"aave-v3","symbol":"USDC","pool":"d9fa8e14-0447-4207-9ae8-7810199dfa1f",
 "tvlUsd":54396850,"apy":2.28092,"apyBase":2.28092,"apyReward":null,"rewardTokens":null,
 "underlyingTokens":["0xaf88d065e77c8cC2239327C5EDb3A432268e5831"],"stablecoin":true,"exposure":"single",
 "apyPct1D":..,"apyPct7D":..,"apyMean30d":..,"mu":..,"sigma":..,"outlier":false,"poolMeta":null,"predictions":{...}}]}
```

- Units: `apy`, `apyBase` and `apyReward` are **percent**. `tvlUsd` is USD.
- `apy = apyBase + apyReward`. For parking, use **`apyBase`**: reward APY is paid in volatile tokens and can be switched off.
  On Base, Aave USDC has `apyReward` 1.85 on top of a base of 4.27.
- Filter with `project=="aave-v3"`, `chain in {"Arbitrum","Base"}`, and match **by `underlyingTokens[0]` address**, not by symbol.
  The USDT symbol is `"USD₮0"` (Unicode). Pool IDs as of 2026-10-20:
  - Arbitrum USDC (native) `d9fa8e14-0447-4207-9ae8-7810199dfa1f`
  - Arbitrum USDT0 (`0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9`) `3a6cc030-738d-4e19-8a40-e63e9c4d5a6f`
  - Base USDC (`0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`) `7e0661bf-8cf3-45e6-9424-31916d4c7b84`
  - Aave v3 on Base lists no USDT pool.

### 2.2 `GET https://yields.llama.fi/chart/{pool}`

About **daily** points from pool listing onward (Arbitrum USDC: 1211 points since 2023-06-28):

```json
{"status":"success","data":[{"timestamp":"2026-10-20T14:01:06.150Z","tvlUsd":54434005,"apy":2.28048,"apyBase":2.28048,
 "apyReward":null,"il7d":null,"apyBase7d":null,"pricePerShare":null}]}
```

Timestamps are ISO and fall at varying times of day, so normalize them to dates.

### 2.3 `GET https://api.llama.fi/protocol/{slug}` (e.g. `aave-v3`)

Large response. The useful keys are `tvl[] {date (unix s), totalLiquidityUSD}`, `chainTvls.{Arbitrum|Base}.tvl[]`
(the same shape; `*-borrowed` keys hold borrows), `currentChainTvls.{Arbitrum,Base}`, and `hacks`.
Example: `currentChainTvls.Arbitrum = 537136360.18`. Daily granularity, with the last point at "now".
Get the slug list from `https://api.llama.fi/protocols`.

### 2.4 Coin prices (peg monitor): `coins.llama.fi`

- Current: `GET /prices/current/{coins}?searchWidth=4h`, where coins are comma-separated `chain:address` or `coingecko:id`.
  ```json
  {"coins":{"arbitrum:0xaf88...5831":{"decimals":6,"symbol":"USDC","price":0.99986,"timestamp":1792504977,"confidence":0.99},
            "coingecko:tether":{"price":0.99911,"symbol":"USDT","timestamp":1792504977,"confidence":0.99}}}
  ```
- Historical point: `GET /prices/historical/{unix_ts}/{coins}`. It returns the nearest price, and the `timestamp` it sends back can differ from the one requested.
- Series: `GET /chart/{coins}?start={unix}&span={n}&period=1d` (also `1h` etc.) returns `{"coins":{id:{"prices":[{timestamp,price}]}}}`.
- Price is a float in USD and timestamps are unix seconds. Reject points with `confidence < 0.9` or a stale `timestamp`.
  DefiLlama aggregates prices with a lag, so it is fine for a peg *monitor* but should not trigger anything in real time.
  Cross-check with an on-chain price (Chainlink/Aave oracle).

---

## 3. Aave v3 on-chain cross-check

| Chain | Pool (proxy) | PoolAddressesProvider |
|---|---|---|
| Arbitrum | `0x794a61358D6845594F94dc1DB02A252b5b4814aD` | `0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb` |
| Base | `0xA238Dd80C259a72e81d7e4664a9801593F98d1c5` | (read `ADDRESSES_PROVIDER()`) |

`getReserveData(address asset)` returns `ReserveDataLegacy` (15 fields, verified with `cast` on both chains):
`(uint256 configuration, uint128 liquidityIndex, uint128 currentLiquidityRate, uint128 variableBorrowIndex,
uint128 currentVariableBorrowRate, uint128 currentStableBorrowRate, uint40 lastUpdateTimestamp, uint16 id,
address aTokenAddress, address stableDebtTokenAddress, address variableDebtTokenAddress,
address interestRateStrategyAddress, uint128 accruedToTreasury, uint128 unbacked, uint128 isolationModeTotalDebt)`.

Live (Arbitrum USDC): `currentLiquidityRate = 22816092784260008821524557`, which is 0.022816, or 2.2816 % APR.

- `currentLiquidityRate` is an **annualized APR in ray (1e27)**, not a per-second rate. Conversion:
  `apr = rate / 1e27`; `apy = (1 + apr / 31_536_000) ** 31_536_000 - 1`, which is about `exp(apr) - 1`.
  Here APY = 2.3078 %. Use `Decimal` for this.
- DefiLlama's `apyBase` (2.2809) matched the **APR** within rounding on Arbitrum, so compare the like-for-like quantity.
  Base did not match as closely (liquidityRate 4.04 % vs DefiLlama 4.27 %, the gap probably due to timing and averaging).
  Alert on a gap of more than 0.5 pp, not on an exact match.
- Realized yield is best measured as the change in `liquidityIndex` (ray) over time. This works on any archive block.

---

## 4. Dated futures (read-only data)

Geo-blocking: Binance `fapi`/`dapi` returned **200** from the dev machine (`api.binance.com/api/v3/ping` was 200 too).
Binance returns **HTTP 451** (or 403 via CloudFront) to US IPs, and the restriction depends on the region. The collector must treat
451/403 as "source unavailable" and keep going without it. OKX and Deribit returned 200 as well.

### Binance COIN-M (`https://dapi.binance.com`) and USD-M (`https://fapi.binance.com`)

- Instruments: `GET /dapi/v1/exchangeInfo` and `GET /fapi/v1/exchangeInfo` (the fapi response is about 1.1 MB). Filter
  `contractType in {CURRENT_QUARTER, NEXT_QUARTER}`.
  - dapi: `{"symbol":"BTCUSD_261225","pair":"BTCUSD","contractType":"CURRENT_QUARTER","deliveryDate":1798185600000,"contractSize":100,"contractStatus":"TRADING","marginAsset":"BTC"}`
  - fapi: `{"symbol":"BTCUSDT_261225","contractType":"CURRENT_QUARTER","deliveryDate":1798185600000,"status":"TRADING","marginAsset":"USDT"}`
  - Note that the status field is `contractStatus` on dapi but `status` on fapi.
- Mark and index: `GET /dapi/v1/premiumIndex?pair=BTCUSD` (or `?symbol=`) and `GET /fapi/v1/premiumIndex?symbol=BTCUSDT_261225`:
  `{"symbol":"BTCUSDT_261225","markPrice":"83238.597","indexPrice":"82438.589","estimatedSettlePrice":"81936.38","time":1792505201000}`.
  Prices are **strings**; times are unix **ms**. Last price: `/fapi/v1/ticker/price`, `/dapi/v1/ticker/price`. History: `/fapi/v1/klines`, `/dapi/v1/klines`.
- Rate limit: 2400 request-weight per minute per IP (from `exchangeInfo.rateLimits`).
- Units: COIN-M is inverse, with a contract face value of 100 USD (BTC) or 10 USD (ETH) and settlement in coin. USD-M settles in USDT.

### OKX (`https://www.okx.com`)

- Instruments: `GET /api/v5/public/instruments?instType=FUTURES&instFamily=BTC-USD` (inverse) or `BTC-USDT` (linear):
  `{"instId":"BTC-USD-261030","alias":"this_month","expTime":"1793347200000","ctVal":"100","ctValCcy":"USD","settleCcy":"BTC","state":"live","ctType":"inverse"}`
- Mark: `GET /api/v5/public/mark-price?instType=FUTURES&instFamily=BTC-USD` returns `{"instId":"BTC-USD-270924","markPx":"86494.8","ts":"..."}`.
- Last: `GET /api/v5/market/ticker?instId=BTC-USD-261225` returns `last, bidPx, askPx, vol24h`.
- Index: `GET /api/v5/market/index-tickers?instId=BTC-USD` returns `idxPx`.
- Envelope is `{"code":"0","data":[...],"msg":""}`. Every value is a string; timestamps are ms. Public limit is about 20 req/2 s per IP per endpoint.

### Deribit (`https://www.deribit.com/api/v2`)

- `GET /public/get_instruments?currency=BTC&kind=future` returns `instrument_name, expiration_timestamp (ms), settlement_period, contract_size, settlement_currency`.
  It includes `BTC-PERPETUAL` (expiration year 3000), which must be excluded.
- `GET /public/get_book_summary_by_currency?currency=BTC&kind=future` returns `instrument_name, mark_price, last, bid_price, ask_price,
  open_interest, estimated_delivery_price, volume_usd`. Prices are numbers (USD for BTC/ETH inverse futures). There are many expiries, including daily and weekly ones.
- Index: `GET /public/get_index_price?index_name=btc_usd` returns `{"result":{"index_price":82322.0,"estimated_delivery_price":82322.0}}`.
- Envelope is JSON-RPC (`result`, `usIn` in µs).

Basis APR = `(F/S − 1) × 365 / days_to_expiry`, with S taken from the same venue's index. Spot carry needs a CEX account, so this is data only.

---

## 5. Gas (incl. L1 data fee)

- **Arbitrum:** `NodeInterface` is a virtual contract at `0x00000000000000000000000000000000000000C8`, called via `eth_call` only:
  `gasEstimateComponents(address to, bool contractCreation, bytes data) returns (uint64 gasEstimate, uint64 gasEstimateForL1, uint256 baseFee, uint256 l1BaseFeeEstimate)`.
  Live result for a USDC transfer: `(45547, 348, 20032000, 1860186)`.
  - `gasEstimate` **already includes** the L1 part, expressed in L2 gas units, so the cost is `gasEstimate × baseFee` wei (about 9.1e11 wei here).
  - Plain `eth_estimateGas` on Arbitrum also includes the L1 component.
  - Set `from` to an address that has the needed balance or allowance, otherwise the call reverts.
- **Base (OP Stack):** `GasPriceOracle` at `0x420000000000000000000000000000000000000F`, `version()` = `1.6.0`; `isEcotone`, `isFjord` and `isIsthmus` are all true.
  - `getL1Fee(bytes signedRawTx)` returns wei. A sample EIP-1559 USDC transfer gave 2 416 569 474 wei (about 2.4e-9 ETH).
  - `getL1FeeUpperBound(uint256 txSize)` returns wei (4 732 051 430 for 200 bytes). Use it when no signed transaction exists yet.
  - `getOperatorFee(uint256 gasUsed)` currently returns 0 but should be included anyway.
  - Total cost = `gasUsed × (baseFee + priorityFee) + l1Fee + operatorFee`.
  - Live L2 gas price was about 0.006 gwei; the L1 fee is usually the larger part.
- **Historical gas (free):** `eth_feeHistory` (up to 1024 blocks back, gives `baseFeePerGas[]`) and block headers
  (`baseFeePerGas`) on any archive RPC. Historical L1 fee comes from transaction receipts (`l1Fee`, `l1GasUsed`, `l1GasPrice`)
  on Base, or `gasUsedForL1` on Arbitrum. No free hosted API for L2 gas history was found (Etherscan-family APIs need a key).
  For backtests, sample block headers once per hour.

---

## 6. Alchemy free tier

As of Alchemy support and pricing pages updated in Aug–Oct 2026: **30 M CU/month, 300 CUPS** (CU per second, app-wide), up to 5 apps,
**full archive data included**, Debug/Trace APIs **not available** on free. Older blog posts quote 300 M CU/month and are outdated.
Some pages say 500 CUPS, which conflicts with the 300 on the pricing table: assume 300.

- Arbitrum and Base mainnet are both on the free tier. Archive `eth_call` and `eth_getStorageAt` at historical blocks work, so
  `anvil --fork-url <alchemy> --fork-block-number N` works.
- Approximate CU costs (Alchemy CU table; verify there): `eth_call` about 26, `eth_getStorageAt` about 17, `eth_getBalance`/`getCode` about 19,
  `eth_getBlockByNumber` about 16, `eth_getLogs` about 75.
- **Budget risk:** a forked simulation of one Pendle swap touches hundreds of storage slots (router, market, SY, oracle, tokens),
  roughly 5–20 k CU each. 30 M CU/month is enough for about 1–3 k fork simulations a month, but **not** for archive-scanning backtests.
  Use anvil's on-disk cache (it caches forked state under `~/.foundry/cache` by default; keep `--no-storage-caching` off) and pin fork blocks.
  On HTTP 429, use exponential backoff and `--compute-units-per-second 300`.
- Public RPCs (`arb1.arbitrum.io/rpc`, `mainnet.base.org`) work for latest-block reads with no key, but are rate-limited and **not archive**.

---

## Decisions for implementation

| Component | Primary source | Notes |
|---|---|---|
| **Collector** (Pendle) | `GET /v2/markets/all?chainId={42161,8453}&isActive=true&limit=100`, plus `GET /v1/prices/assets?chainId=…` every 5–15 min | Store `impliedApy`, `liquidity`, `totalTvl`, `totalPt`, `totalSy`, PT USD price, accounting-asset USD price, and the snapshot time (our clock). About 4 CU per chain per cycle. Refresh `/v1/assets/all` daily for decimals and expiry. |
| **Backtest** | `GET /v3/{chain}/markets/{addr}/historical-data?time_frame=hour&fields=timestamp,impliedApy,ptPrice,syPrice,tvl,totalPt,totalSy,underlyingApy,tradingVolume`, windows of at most 1440 points, for every `isActive=false` market | Covers matured markets back to 2023. Convert `ptPrice` from USD into accounting-asset units. Drop the last days before expiry and the low-TVL start. |
| **Slippage** (live) | `POST /v3/sdk/{chain}/convert`, quoting at sizes {1k, 10k, 50k} USD, with both `useLimitOrder` true and false | Use `effectiveApy` and `priceImpact`. Feed `tx.to`/`tx.data` to the Anvil fork simulation. Check `tx.to == 0x8888…F946`. |
| **Slippage** (backtest) | Our own AMM model using hourly `totalPt`/`totalSy`/`impliedApy` plus market params | The API does not give historical quotes. |
| **Parking** | DefiLlama `/pools` (`apyBase`, by pool ID) for trend, plus on-chain `getReserveData` on the Aave Pool for the live rate and `liquidityIndex` | On-chain is authoritative; flag a gap of more than 0.5 pp. Use `/chart/{pool}` for history. |
| **Peg monitor** | `coins.llama.fi/prices/current` (USDC/USDT plus PT accounting assets) and `/chart` for history | Cross-check with an on-chain oracle. Ignore points with low `confidence` or a stale `timestamp`. |
| **TVL trend / protocol risk** | `api.llama.fi/protocol/{slug}` `chainTvls.{chain}.tvl[]` | Daily granularity. |
| **Futures** (data only) | Deribit `get_book_summary_by_currency` and `get_index_price`; OKX `public/instruments` with `public/mark-price` and `index-tickers`; Binance `exchangeInfo` with `premiumIndex` | Treat Binance 451/403 as "source down". Exclude perpetuals. |
| **Gas** | Arbitrum `NodeInterface.gasEstimateComponents`; Base `eth_estimateGas` plus `GasPriceOracle.getL1Fee`/`getL1FeeUpperBound` plus `getOperatorFee`; history from block headers / `eth_feeHistory` | Convert to USD with an ETH price from DefiLlama coins. |
| **RPC** | Alchemy free (archive) for forks; public RPC for latest reads | Pin fork blocks and cache. Watch the 30 M CU/month budget. |

### Known gaps

1. **Historical liquidity depth for expired markets** exists only as hourly `totalPt`/`totalSy`/`tvl`. There is no historical
   limit-order book and no historical `convert` quote, so backtest slippage is modeled from AMM math, not observed.
2. No sub-hour Pendle history. Intra-hour implied-APY spikes are invisible to the backtest.
3. `historical-data.ptPrice` is in USD only. Pricing in underlying units depends on our own conversion via `syPrice`, the accounting-asset price, or the SY exchange rate.
4. **Small Pendle universe on the target chains:** 4 active markets on Arbitrum and 8 on Base today, many of them exotic (RWA, tokenized equities,
   points tokens) and thin (often under $100k liquidity). This is a sample-size and risk problem for the research.
5. Pendle free rate limits (200 CU/min and 200k CU/week observed) cannot be relied on as a fixed figure. Read the headers.
6. DefiLlama free-tier rate limits are undocumented. `/pools` is 11 MB per call, and `/chart` is daily only.
7. Aave `apyBase` on DefiLlama is not exactly equal to the on-chain APY (it is closer to APR, plus timing lag).
8. Binance availability depends on the region (451). Spot carry on CEXs is out of scope and data only.
9. Alchemy free has no Debug/Trace APIs and only 30 M CU/month, so a full on-chain historical replay is not affordable on the free tier.
10. No free hosted history of L2 gas or L1 data fees. It has to be rebuilt from block headers and receipts.
