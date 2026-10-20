from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx

from ballast.sources.futures import BasisSnapshot, BinanceUsdm, Deribit, Okx, collect_basis
from ballast.sources.http import JsonHttpClient

NOW = datetime(2026, 10, 20, tzinfo=UTC)
EXP_MS = int((NOW + timedelta(days=73)).timestamp() * 1000)


def test_basis_apr() -> None:
    snap = BasisSnapshot("x", "BTC-X", "BTC", NOW + timedelta(days=73), Decimal(101), Decimal(100), NOW)
    assert snap.basis_apr == Decimal("0.05")
    near = BasisSnapshot("x", "BTC-Y", "BTC", NOW + timedelta(days=1), Decimal(101), Decimal(100), NOW)
    assert near.basis_apr is None


def routes(table: dict[str, object]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = table.get(request.url.path)
        return httpx.Response(200, json=body) if body is not None else httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_deribit_excludes_perpetual() -> None:
    table = {
        "/public/get_index_price": {"result": {"index_price": 100.0}},
        "/public/get_instruments": {
            "result": [
                {"instrument_name": "BTC-1JAN27", "expiration_timestamp": EXP_MS},
                {"instrument_name": "BTC-PERPETUAL", "expiration_timestamp": 32503680000000},
            ]
        },
        "/public/get_book_summary_by_currency": {
            "result": [
                {"instrument_name": "BTC-1JAN27", "mark_price": 101.0},
                {"instrument_name": "BTC-PERPETUAL", "mark_price": 100.1},
            ]
        },
    }
    async with JsonHttpClient("https://d", transport=routes(table)) as http:
        snaps = await Deribit(http).snapshots(NOW)
    assert {s.instrument for s in snaps} == {"BTC-1JAN27"}  # same instruments returned for ETH call too
    assert snaps[0].basis_apr == Decimal("0.05")


async def test_okx() -> None:
    table = {
        "/api/v5/public/instruments": {
            "data": [{"instId": "BTC-USD-270101", "expTime": str(EXP_MS), "state": "live"}]
        },
        "/api/v5/public/mark-price": {"data": [{"instId": "BTC-USD-270101", "markPx": "101"}]},
        "/api/v5/market/index-tickers": {"data": [{"idxPx": "100"}]},
    }
    async with JsonHttpClient("https://o", transport=routes(table)) as http:
        snaps = await Okx(http).snapshots(NOW)
    assert snaps[0].future_price == Decimal(101)


async def test_binance_geoblock_is_skipped() -> None:
    blocked = httpx.MockTransport(lambda r: httpx.Response(451))
    async with JsonHttpClient("https://b", transport=blocked, max_retries=0) as http:
        assert await collect_basis([BinanceUsdm(http)], NOW) == []
