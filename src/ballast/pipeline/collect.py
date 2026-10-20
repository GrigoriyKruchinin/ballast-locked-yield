"""Collection step: fetch everything one scan needs and persist raw snapshots.

Every source is optional: a failing source is logged and recorded in ``CollectedData.errors``,
never silently filled with defaults.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from ballast.chains.client import JsonRpcChainClient, RpcError
from ballast.chains.gas import GasCollector
from ballast.config.whitelist import Whitelist
from ballast.costs.model import GasSnapshot
from ballast.domain.models import Chain, Opportunity, utcnow
from ballast.logging import get_logger
from ballast.sources.defillama import (
    DefiLlamaPrices,
    DefiLlamaTvl,
    DefiLlamaYields,
    ParkingPool,
    ParkingSource,
    ProtocolTvl,
    peg_coin_ids,
)
from ballast.sources.futures import BasisSnapshot, IFuturesVenue, collect_basis
from ballast.sources.http import SourceUnavailableError
from ballast.sources.pendle import PendleClient, PendleMarketSnapshot, PendleNormalizer, snapshot_chain
from ballast.storage.repositories import SnapshotRepository

log = get_logger(__name__)

ETH_COIN = "coingecko:ethereum"


@dataclass(slots=True)
class CollectedData:
    observed_at: datetime
    pendle: list[PendleMarketSnapshot] = field(default_factory=list)
    opportunities: list[Opportunity] = field(default_factory=list)
    parking_pools: list[ParkingPool] = field(default_factory=list)
    parking: list[Opportunity] = field(default_factory=list)
    gas: dict[Chain, GasSnapshot] = field(default_factory=dict)
    pegs: dict[str, Decimal] = field(default_factory=dict)
    tvl: dict[str, ProtocolTvl] = field(default_factory=dict)
    basis: list[BasisSnapshot] = field(default_factory=list)
    eth_usd: Decimal | None = None
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Collector:
    whitelist: Whitelist
    pendle: PendleClient
    yields: DefiLlamaYields
    prices: DefiLlamaPrices
    tvl: DefiLlamaTvl
    chain_clients: Sequence[JsonRpcChainClient]
    futures: Sequence[IFuturesVenue]
    portfolio_chains: Sequence[Chain]
    observe_chain_ids: Sequence[int] = ()
    gas_collector: GasCollector = field(default_factory=GasCollector)

    async def collect(self, *, include_tvl: bool, include_basis: bool = True) -> CollectedData:
        data = CollectedData(observed_at=utcnow())
        await self._pendle(data)
        await self._parking(data)
        await self._prices(data)
        await self._gas(data)
        if include_tvl:
            await self._tvl(data)
        if include_basis:
            data.basis = await collect_basis(self.futures, data.observed_at)
        return data

    async def _pendle(self, data: CollectedData) -> None:
        normalizer = PendleNormalizer(self.whitelist)
        chain_ids = [int(c) for c in self.portfolio_chains] + [
            c for c in self.observe_chain_ids if c not in {int(x) for x in self.portfolio_chains}
        ]
        for chain_id in chain_ids:
            try:
                snaps = await snapshot_chain(self.pendle, chain_id)
            except SourceUnavailableError as exc:
                data.errors.append(f"pendle {chain_id}: {exc}")
                continue
            data.pendle.extend(snaps)
        data.opportunities = [o for s in data.pendle if (o := normalizer.normalize(s)) is not None]

    async def _parking(self, data: CollectedData) -> None:
        source = ParkingSource(self.yields, self.portfolio_chains)
        data.parking = list(await source.fetch())
        data.parking_pools = source.last_pools
        if not data.parking:
            data.errors.append("parking: no pools")

    async def _prices(self, data: CollectedData) -> None:
        coins = peg_coin_ids(self.whitelist)
        coins["eth"] = ETH_COIN
        try:
            prices = await self.prices.current(coins)
        except SourceUnavailableError as exc:
            data.errors.append(f"prices: {exc}")
            return
        data.eth_usd = prices.pop("eth", None)
        data.pegs = prices

    async def _gas(self, data: CollectedData) -> None:
        if data.eth_usd is None:
            data.errors.append("gas: no ETH price")
            return
        for client in self.chain_clients:
            try:
                data.gas[client.chain] = await self.gas_collector.snapshot(client, data.eth_usd)
            except RpcError as exc:
                data.errors.append(f"gas {client.chain.slug}: {exc}")

    async def _tvl(self, data: CollectedData) -> None:
        for slug, info in self.whitelist.protocols.items():
            if info.llama_slug is None:
                continue
            try:
                tvl = await self.tvl.protocol_tvl(info.llama_slug)
            except SourceUnavailableError as exc:
                data.errors.append(f"tvl {slug}: {exc}")
                continue
            if tvl is not None:
                data.tvl[slug] = tvl


def whitelisted_markets(data: CollectedData, whitelist: Whitelist) -> set[str]:
    out: set[str] = set()
    for opp in data.opportunities:
        if opp.market_address and whitelist.find_asset(opp.asset_symbol, opp.chain, opp.asset_address):
            out.add(opp.market_address.lower())
    return out


def persist(data: CollectedData, repo: SnapshotRepository, whitelist: Whitelist) -> None:
    repo.add_pendle(data.pendle, whitelisted_markets(data, whitelist))
    repo.add_parking(data.parking_pools)
    repo.add_gas(data.gas.values(), data.observed_at)
    repo.add_pegs(data.pegs, data.observed_at)
    repo.add_basis(data.basis)
    for protocol, tvl in data.tvl.items():
        repo.add_tvl(protocol, tvl)


TVL_REFRESH = timedelta(hours=20)
