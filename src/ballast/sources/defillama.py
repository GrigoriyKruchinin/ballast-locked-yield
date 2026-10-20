"""DefiLlama sources: parking rates (yields), protocol TVL and stablecoin peg prices.

Units: ``apy``/``apyBase`` are percent; we convert to fractions. ``apyBase`` is used for parking
because reward APY is paid in volatile tokens and can be switched off.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ballast.config.whitelist import Whitelist
from ballast.domain.models import Chain, Opportunity, SourceKind, utcnow
from ballast.domain.money import HUNDRED
from ballast.logging import get_logger
from ballast.sources.http import JsonHttpClient, SourceUnavailableError
from ballast.sources.parsing import dec, from_unix

log = get_logger(__name__)

LLAMA_CHAIN_NAMES: Mapping[Chain, str] = {
    Chain.ETHEREUM: "Ethereum",
    Chain.ARBITRUM: "Arbitrum",
    Chain.BASE: "Base",
}
MIN_PRICE_CONFIDENCE = Decimal("0.9")
MAX_PRICE_AGE = timedelta(hours=6)


@dataclass(frozen=True, slots=True)
class ParkingPool:
    pool_id: str
    protocol: str
    chain: Chain
    asset_symbol: str
    asset_address: str
    tvl_usd: Decimal
    apy_base: Decimal  # fraction
    apy_reward: Decimal | None  # fraction
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class ProtocolTvl:
    slug: str
    tvl_usd: Decimal
    tvl_7d_ago_usd: Decimal | None
    observed_at: datetime

    @property
    def change_7d(self) -> Decimal | None:
        if not self.tvl_7d_ago_usd:
            return None
        return self.tvl_usd / self.tvl_7d_ago_usd - 1


def _pct(value: object) -> Decimal | None:
    parsed = dec(value)
    return None if parsed is None else parsed / HUNDRED


@dataclass(slots=True)
class DefiLlamaYields:
    """Parking pools: whitelisted protocol + pinned underlying address + core market only.

    Pools with ``poolMeta`` (e.g. Aave "Umbrella" slashable staking, "Prime"/"Horizon" instances,
    Compound "WETH-pool") are not plain lending deposits and are excluded, as are small pools.
    """

    http: JsonHttpClient
    whitelist: Whitelist
    min_pool_tvl_usd: Decimal = Decimal(20_000_000)

    async def parking_pools(self, chains: Sequence[Chain]) -> list[ParkingPool]:
        body = await self.http.get("/pools")
        rows = body.get("data", []) if isinstance(body, dict) else []
        wanted: dict[tuple[str, Chain, str], str] = {}
        slug_to_protocol = {
            (info.llama_slug or name): name for name, info in self.whitelist.protocols.items()
        }
        for park in self.whitelist.parking:
            asset = next(a for a in self.whitelist.assets if a.symbol == park.asset)
            for park_chain in park.chains:
                address = asset.address_on(park_chain)
                if park_chain in chains and address is not None:
                    project = self.whitelist.protocols[park.protocol].llama_slug or park.protocol
                    wanted[(project, park_chain, address.lower())] = asset.symbol
        by_name = {name: chain for chain, name in LLAMA_CHAIN_NAMES.items()}
        now = utcnow()
        pools: list[ParkingPool] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            chain = by_name.get(str(row.get("chain")))
            tokens = row.get("underlyingTokens")
            if chain is None or not isinstance(tokens, list) or len(tokens) != 1:
                continue
            key = (str(row.get("project")), chain, str(tokens[0]).lower())
            symbol = wanted.get(key)
            apy_base = _pct(row.get("apyBase"))
            tvl = dec(row.get("tvlUsd"))
            if symbol is None or apy_base is None or tvl is None:
                continue
            if row.get("poolMeta") is not None or tvl < self.min_pool_tvl_usd:
                continue
            pools.append(
                ParkingPool(
                    pool_id=str(row.get("pool")),
                    protocol=slug_to_protocol.get(key[0], key[0]),
                    chain=chain,
                    asset_symbol=symbol,
                    asset_address=str(tokens[0]),
                    tvl_usd=tvl,
                    apy_base=apy_base,
                    apy_reward=_pct(row.get("apyReward")),
                    observed_at=now,
                )
            )
        return pools

    async def pool_history(self, pool_id: str) -> list[tuple[datetime, Decimal]]:
        """Daily ``(timestamp, apyBase fraction)`` points."""
        body = await self.http.get(f"/chart/{pool_id}")
        rows = body.get("data", []) if isinstance(body, dict) else []
        out: list[tuple[datetime, Decimal]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            ts = row.get("timestamp")
            apy = _pct(row.get("apyBase"))
            if isinstance(ts, str) and apy is not None:
                out.append((datetime.fromisoformat(ts.replace("Z", "+00:00")), apy))
        return out


def parking_opportunity(pool: ParkingPool) -> Opportunity:
    return Opportunity(
        key=f"parking:{pool.protocol}:{pool.chain.slug}:{pool.asset_symbol}:{pool.pool_id[:8]}",
        kind=SourceKind.PARKING,
        chain=pool.chain,
        venue=pool.protocol,
        protocol=pool.protocol,
        asset_symbol=pool.asset_symbol,
        asset_address=pool.asset_address,
        entry_price=Decimal(1),
        payoff_per_unit=Decimal(1),
        maturity=None,
        observed_at=pool.observed_at,
        liquidity_usd=pool.tvl_usd,
        quoted_apy=pool.apy_base,
        extra={"pool_id": pool.pool_id},
    )


@dataclass(slots=True)
class ParkingSource:
    """Implements :class:`ballast.domain.interfaces.ILockedYieldSource` for parking venues."""

    yields: DefiLlamaYields
    chains: Sequence[Chain]
    last_pools: list[ParkingPool] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "parking"

    async def fetch(self) -> Sequence[Opportunity]:
        try:
            self.last_pools = await self.yields.parking_pools(self.chains)
        except SourceUnavailableError as exc:
            log.warning("parking_fetch_failed", error=str(exc))
            return []
        return [parking_opportunity(p) for p in self.last_pools]


@dataclass(slots=True)
class DefiLlamaTvl:
    http: JsonHttpClient

    async def protocol_tvl(self, slug: str) -> ProtocolTvl | None:
        body = await self.http.get(f"/protocol/{slug}")
        series = body.get("tvl", []) if isinstance(body, dict) else []
        points: list[tuple[datetime, Decimal]] = []
        for row in series:
            if not isinstance(row, dict):
                continue
            ts = from_unix(row.get("date"))
            value = dec(row.get("totalLiquidityUSD"))
            if ts is not None and value is not None:
                points.append((ts, value))
        if not points:
            return None
        points.sort()
        last_ts, last = points[-1]
        target = last_ts - timedelta(days=7)
        earlier = [v for ts, v in points if ts <= target]
        return ProtocolTvl(slug, last, earlier[-1] if earlier else None, last_ts)


@dataclass(slots=True)
class DefiLlamaPrices:
    http: JsonHttpClient

    async def current(self, coins: Mapping[str, str]) -> dict[str, Decimal]:
        """Prices for ``{label: "chain:address"}``; low-confidence or stale points are dropped."""
        if not coins:
            return {}
        ids = ",".join(sorted(set(coins.values())))
        body = await self.http.get(f"/prices/current/{ids}", {"searchWidth": "4h"})
        found = body.get("coins", {}) if isinstance(body, dict) else {}
        now = datetime.now(UTC)
        out: dict[str, Decimal] = {}
        for label, coin_id in coins.items():
            row = found.get(coin_id)
            if not isinstance(row, dict):
                continue
            price = dec(row.get("price"))
            confidence = dec(row.get("confidence"))
            ts = from_unix(row.get("timestamp"))
            if price is None or (confidence is not None and confidence < MIN_PRICE_CONFIDENCE):
                continue
            if ts is not None and now - ts > MAX_PRICE_AGE:
                continue
            out[label] = price
        return out


def peg_coin_ids(whitelist: Whitelist) -> dict[str, str]:
    """``{lowercase peg symbol: "chain:address"}`` using the first pinned address of each peg asset."""
    out: dict[str, str] = {}
    peg_symbols = {a.peg_symbol for a in whitelist.assets}
    for asset in whitelist.assets:
        if asset.symbol not in peg_symbols:
            continue
        for chain, address in sorted(asset.addresses.items()):
            out[asset.symbol.lower()] = f"{chain.slug}:{address}"
            break
    return out
