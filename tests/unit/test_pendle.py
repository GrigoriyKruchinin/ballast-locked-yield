import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from ballast.config.whitelist import load_whitelist
from ballast.domain.models import Chain, SourceKind
from ballast.sources.http import JsonHttpClient, SourceUnavailableError
from ballast.sources.parsing import dec, split_asset_id
from ballast.sources.pendle import PendleClient, PendleNormalizer, PendleSource, snapshot_chain

WL = load_whitelist(Path(__file__).resolve().parents[2] / "config" / "whitelist.yaml")
SUSDE = "0x9d39a5de30e57443bff2a8307a4256c8797a3497"
USDE = "0x4c9edd5852cd905f086c759e8383e09bff1e68b3"
PT = "0x1111111111111111111111111111111111111111"


def market(
    chain: int = 1, name: str = "sUSDe", expiry: str = "2030-01-01T00:00:00.000Z"
) -> dict[str, object]:
    return {
        "name": name,
        "protocol": "Ethena",
        "chainId": chain,
        "address": "0xMARKET",
        "expiry": expiry,
        "pt": f"{chain}-{PT}",
        "yt": f"{chain}-0x2222222222222222222222222222222222222222",
        "sy": f"{chain}-0x3333333333333333333333333333333333333333",
        "underlyingAsset": f"{chain}-{SUSDE}",
        "accountingAsset": f"{chain}-{USDE}",
        "details": {
            "liquidity": 3530301.5,
            "totalTvl": 9e6,
            "impliedApy": 0.0508,
            "totalPt": 1.5e6,
            "totalSy": 1e6,
        },
        "categoryIds": ["stables"],
    }


def transport(
    markets: list[dict[str, object]], prices: dict[str, float], status: int = 200
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status)
        if request.url.path.endswith("/v2/markets/all"):
            return httpx.Response(
                200,
                json={"total": len(markets), "results": markets},
                headers={"x-ratelimit-remaining": "150", "x-ratelimit-reset": "0"},
            )
        if request.url.path.endswith("/v1/prices/assets"):
            return httpx.Response(200, json={"prices": prices})
        if "historical-data" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"timestamp": "2026-01-01T00:00:00.000Z", "impliedApy": 0.05, "ptPrice": 0.99}
                    ]
                },
            )
        if request.url.path.endswith("/convert"):
            body = json.loads(request.content)
            assert body["useLimitOrder"] is False
            return httpx.Response(
                200,
                json={
                    "requiredApprovals": [{"token": USDE, "amount": "1000"}],
                    "routes": [
                        {
                            "tx": {"to": "0x888888888889758F76e7103c6CbF23ABbF58F946", "data": "0xabc"},
                            "outputs": [{"token": PT, "amount": "1005"}],
                            "data": {
                                "priceImpact": -0.0012,
                                "effectiveApy": 0.049,
                                "fee": {"usd": 0.6},
                                "gasUsed": "943846",
                            },
                        }
                    ],
                },
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


PRICES = {f"1-{PT}": 0.99482 * 0.9997, f"1-{USDE}": 0.9997, f"1-{SUSDE}": 1.2}


async def test_snapshot_and_normalize() -> None:
    async with JsonHttpClient("https://x", transport=transport([market()], PRICES)) as http:
        snaps = await snapshot_chain(PendleClient(http), 1)
    assert len(snaps) == 1
    snap = snaps[0]
    assert snap.is_stable
    assert snap.pt_price_in_accounting is not None
    assert abs(snap.pt_price_in_accounting - Decimal("0.99482")) < Decimal("1e-12")
    opp = PendleNormalizer(WL).normalize(snap)
    assert opp is not None
    assert opp.kind is SourceKind.PENDLE_PT
    assert opp.chain is Chain.ETHEREUM
    assert opp.protocol == "ethena"
    assert opp.venue == "pendle"
    assert opp.quoted_apy == Decimal("0.0508")
    assert opp.key == "pendle:ethereum:0xmarket"


async def test_observe_only_chain_not_normalized() -> None:
    m = market(chain=9745)
    prices = {k.replace("1-", "9745-"): v for k, v in PRICES.items()}
    async with JsonHttpClient("https://x", transport=transport([m], prices)) as http:
        snaps = await snapshot_chain(PendleClient(http), 9745)
    assert len(snaps) == 1
    assert PendleNormalizer(WL).normalize(snaps[0]) is None


async def test_missing_price_or_expired_skipped() -> None:
    async with JsonHttpClient("https://x", transport=transport([market()], {})) as http:
        snaps = await snapshot_chain(PendleClient(http), 1)
    assert PendleNormalizer(WL).normalize(snaps[0]) is None
    expired = market(expiry="2020-01-01T00:00:00.000Z")
    async with JsonHttpClient("https://x", transport=transport([expired], PRICES)) as http:
        snaps = await snapshot_chain(PendleClient(http), 1)
    assert not snaps[0].is_active
    assert PendleNormalizer(WL).normalize(snaps[0]) is None


async def test_unknown_asset_keeps_protocol_slug() -> None:
    m = market(name="USD3")
    m["protocol"] = "Reserve Protocol"
    async with JsonHttpClient("https://x", transport=transport([m], PRICES)) as http:
        snaps = await snapshot_chain(PendleClient(http), 1)
    opp = PendleNormalizer(WL).normalize(snaps[0])
    assert opp is not None
    assert opp.protocol == "reserve-protocol"


async def test_history_and_quote() -> None:
    async with JsonHttpClient("https://x", transport=transport([], {})) as http:
        client = PendleClient(http)
        points = await client.history(
            1, "0xm", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)
        )
        quote = await client.quote(
            1,
            USDE,
            1000,
            PT,
            receiver="0x000000000000000000000000000000000000dEaD",
            slippage=Decimal("0.005"),
            use_limit_order=False,
        )
    assert points[0].pt_usd == Decimal("0.99")
    assert quote is not None
    assert quote.price_impact == Decimal("-0.0012")
    assert quote.gas_used == 943846
    assert quote.required_approvals == ((USDE, 1000),)


async def test_source_survives_upstream_failure() -> None:
    http = JsonHttpClient("https://x", transport=transport([], {}, status=451), max_retries=0)
    async with http:
        source = PendleSource(PendleClient(http), PendleNormalizer(WL), [Chain.ETHEREUM])
        assert await source.fetch() == []


async def test_retries_then_unavailable() -> None:
    async with JsonHttpClient("https://x", transport=transport([], {}, status=503), max_retries=0) as http:
        with pytest.raises(SourceUnavailableError):
            await PendleClient(http).markets(1, active=True)


def test_parsing_helpers() -> None:
    assert dec(0.1) == Decimal("0.1")
    assert dec("1e-3") == Decimal("0.001")
    assert dec(float("nan")) is None
    assert dec(True) is None
    assert dec("abc") is None
    assert split_asset_id("1-0xabc") == (1, "0xabc")
    assert split_asset_id("bad") is None
