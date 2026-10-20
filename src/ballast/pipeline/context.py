"""Composition root: builds clients and services for a role from its settings (DI)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ballast.chains.client import JsonRpcChainClient
from ballast.config.settings import RoleSettings
from ballast.config.whitelist import Whitelist, load_whitelist
from ballast.domain.models import Chain
from ballast.pipeline.analyze import Analyzer
from ballast.pipeline.collect import Collector
from ballast.risk.incidents import IncidentCatalog, load_incidents
from ballast.sources.defillama import DefiLlamaPrices, DefiLlamaTvl, DefiLlamaYields
from ballast.sources.futures import BinanceUsdm, Deribit, IFuturesVenue, Okx
from ballast.sources.http import JsonHttpClient
from ballast.sources.pendle import PendleClient
from ballast.storage.db import make_engine, make_sessionmaker

# Pendle chains observed for the report only (not traded): see docs/decisions.md.
OBSERVE_ONLY_CHAIN_IDS = (10, 56, 143, 146, 196, 999, 4663, 5000, 9745, 80094)


@dataclass(frozen=True, slots=True)
class Book:
    """A paper portfolio restricted to a set of chains."""

    name: str
    chains: tuple[Chain, ...]


def books(enabled: tuple[Chain, ...]) -> tuple[Book, ...]:
    l2 = tuple(c for c in enabled if c is not Chain.ETHEREUM)
    out = [Book("all", enabled)]
    if l2 and l2 != enabled:
        out.append(Book("l2", l2))
    return tuple(out)


@dataclass(frozen=True, slots=True)
class AppContext:
    settings: RoleSettings
    whitelist: Whitelist
    catalog: IncidentCatalog
    sessions: async_sessionmaker[AsyncSession]
    collector: Collector
    analyzer: Analyzer
    pendle: PendleClient
    chain_clients: dict[Chain, JsonRpcChainClient]
    books: tuple[Book, ...]


@asynccontextmanager
async def app_context(settings: RoleSettings) -> AsyncIterator[AppContext]:
    s = settings
    if (
        s.db is None
        or s.chains is None
        or s.data is None
        or s.costs is None
        or s.risk is None
        or s.allocation is None
    ):
        msg = "role lacks settings needed for scanning"
        raise RuntimeError(msg)
    whitelist = load_whitelist(s.app.whitelist_path)
    catalog = load_incidents(s.app.incidents_path)
    engine = make_engine(s.db.url())
    timeout = s.data.http_timeout_seconds
    retries = s.data.http_max_retries
    async with AsyncExitStack() as stack:

        async def http(url: str, slow: bool = False) -> JsonHttpClient:
            client = JsonHttpClient(url, timeout_seconds=timeout * (3 if slow else 1), max_retries=retries)
            return await stack.enter_async_context(client)

        pendle = PendleClient(await http(s.data.pendle_api_url))
        yields = DefiLlamaYields(await http(s.data.defillama_yields_url, slow=True), whitelist)
        prices = DefiLlamaPrices(await http(s.data.defillama_coins_url))
        tvl = DefiLlamaTvl(await http(s.data.defillama_api_url, slow=True))
        futures: list[IFuturesVenue] = [
            Deribit(await http("https://www.deribit.com/api/v2")),
            Okx(await http("https://www.okx.com")),
            BinanceUsdm(await http("https://fapi.binance.com")),
        ]
        chain_clients: dict[Chain, JsonRpcChainClient] = {}
        for chain in s.chains.chain_enabled:
            url = s.chains.rpc_url(chain).get_secret_value()
            if not url:
                continue
            client = JsonRpcChainClient(chain, url, timeout_seconds=s.chains.rpc_timeout_seconds)
            chain_clients[chain] = await stack.enter_async_context(client)
        collector = Collector(
            whitelist=whitelist,
            pendle=pendle,
            yields=yields,
            prices=prices,
            tvl=tvl,
            chain_clients=list(chain_clients.values()),
            futures=futures,
            portfolio_chains=list(s.chains.chain_enabled),
            observe_chain_ids=OBSERVE_ONLY_CHAIN_IDS,
        )
        analyzer = Analyzer(whitelist, catalog, s.risk, s.allocation, s.costs)
        try:
            yield AppContext(
                settings=s,
                whitelist=whitelist,
                catalog=catalog,
                sessions=make_sessionmaker(engine),
                collector=collector,
                analyzer=analyzer,
                pendle=pendle,
                chain_clients=chain_clients,
                books=books(tuple(s.chains.chain_enabled)),
            )
        finally:
            await engine.dispose()
