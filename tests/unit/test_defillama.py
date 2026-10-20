import time
from decimal import Decimal
from pathlib import Path

import httpx

from ballast.config.whitelist import load_whitelist
from ballast.domain.models import Chain, SourceKind
from ballast.sources.defillama import (
    DefiLlamaPrices,
    DefiLlamaTvl,
    DefiLlamaYields,
    ParkingSource,
    peg_coin_ids,
)
from ballast.sources.http import JsonHttpClient

WL = load_whitelist(Path(__file__).resolve().parents[2] / "config" / "whitelist.yaml")
ARB_USDC = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"

POOLS = [
    {
        "chain": "Arbitrum",
        "project": "aave-v3",
        "symbol": "USDC",
        "pool": "p1",
        "tvlUsd": 5.4e7,
        "apyBase": 2.28,
        "apyReward": None,
        "underlyingTokens": [ARB_USDC],
    },
    # fake token with same symbol must be ignored
    {
        "chain": "Arbitrum",
        "project": "aave-v3",
        "symbol": "USDC",
        "pool": "p2",
        "tvlUsd": 1e6,
        "apyBase": 50.0,
        "underlyingTokens": ["0x000000000000000000000000000000000000dEaD"],
    },
    {
        "chain": "Base",
        "project": "unknown-lender",
        "symbol": "USDC",
        "pool": "p3",
        "tvlUsd": 1e6,
        "apyBase": 9.0,
        "underlyingTokens": ["0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"],
    },
    {"chain": "Solana", "project": "aave-v3", "pool": "p4", "apyBase": 3, "underlyingTokens": ["x"]},
    # slashable staking / non-core instance must be ignored even with the right token
    {
        "chain": "Arbitrum",
        "project": "aave-v3",
        "pool": "p5",
        "tvlUsd": 9e7,
        "apyBase": 6.0,
        "poolMeta": "Umbrella",
        "underlyingTokens": [ARB_USDC],
    },
    # too small
    {
        "chain": "Arbitrum",
        "project": "compound-v3",
        "pool": "p6",
        "tvlUsd": 1.9e6,
        "apyBase": 3.1,
        "underlyingTokens": [ARB_USDC],
    },
    # sky savings via llama slug
    {
        "chain": "Arbitrum",
        "project": "sky-lending",
        "symbol": "SUSDS",
        "pool": "p7",
        "tvlUsd": 3.6e8,
        "apyBase": 3.8,
        "underlyingTokens": ["0x6491c05a82219b8d1479057361ff1654749b876b"],
    },
]


def mock(handler_map: dict[str, object]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        for prefix, body in handler_map.items():
            if request.url.path.startswith(prefix):
                return httpx.Response(200, json=body)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_parking_matches_by_address() -> None:
    async with JsonHttpClient("https://y", transport=mock({"/pools": {"data": POOLS}})) as http:
        source = ParkingSource(DefiLlamaYields(http, WL), [Chain.ARBITRUM, Chain.BASE])
        opps = await source.fetch()
    assert {o.key for o in opps} == {"parking:aave-v3:arbitrum:USDC:p1", "parking:sky:arbitrum:USDS:p7"}
    opp = next(o for o in opps if o.protocol == "aave-v3")
    assert opp.kind is SourceKind.PARKING
    assert opp.quoted_apy == Decimal("0.0228")
    assert opp.maturity is None
    assert opp.key == "parking:aave-v3:arbitrum:USDC:p1"


async def test_parking_source_failure_returns_empty() -> None:
    async with JsonHttpClient(
        "https://y", transport=httpx.MockTransport(lambda r: httpx.Response(451))
    ) as http:
        assert await ParkingSource(DefiLlamaYields(http, WL), [Chain.ARBITRUM]).fetch() == []


async def test_protocol_tvl_change() -> None:
    day = 86400
    now = int(time.time()) // day * day
    series = [{"date": now - i * day, "totalLiquidityUSD": 100 - i} for i in range(10)]
    async with JsonHttpClient("https://a", transport=mock({"/protocol/aave-v3": {"tvl": series}})) as http:
        tvl = await DefiLlamaTvl(http).protocol_tvl("aave-v3")
    assert tvl is not None
    assert tvl.tvl_usd == 100
    assert tvl.tvl_7d_ago_usd == 93
    assert tvl.change_7d == Decimal(100) / Decimal(93) - 1


async def test_prices_filter_confidence_and_staleness() -> None:
    now = int(time.time())
    body = {
        "coins": {
            "ethereum:0xa": {"price": 0.9998, "confidence": 0.99, "timestamp": now},
            "ethereum:0xb": {"price": 0.5, "confidence": 0.2, "timestamp": now},
            "ethereum:0xc": {"price": 1.0, "confidence": 0.99, "timestamp": now - 86400},
        }
    }
    async with JsonHttpClient("https://c", transport=mock({"/prices/current": body})) as http:
        prices = await DefiLlamaPrices(http).current(
            {
                "usdc": "ethereum:0xa",
                "bad": "ethereum:0xb",
                "stale": "ethereum:0xc",
                "missing": "ethereum:0xd",
            }
        )
    assert prices == {"usdc": Decimal("0.9998")}


def test_peg_coin_ids() -> None:
    ids = peg_coin_ids(WL)
    assert ids["usde"] == "ethereum:0x4c9EDD5852cd905f086C759E8383e09bff1E68B3"
    assert "susde" not in ids
    assert set(ids) == {"usdc", "usdt", "dai", "usds", "usde"}
