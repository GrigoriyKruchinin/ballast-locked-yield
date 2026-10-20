import os
import warnings
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import exc as sa_exc
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ballast.costs.model import GasSnapshot
from ballast.domain.models import Chain, Decision, DecisionAction, MonitorEvent, Severity, utcnow
from ballast.sources.pendle import PendleHistoryPoint, PendleMarketSnapshot
from ballast.storage.db import make_engine, make_sessionmaker, session_scope
from ballast.storage.models import Base
from ballast.storage.repositories import (
    HistoryRepository,
    MonitorRepository,
    ScanRepository,
    SnapshotRepository,
)

URL = os.environ.get("TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:")


@pytest.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    warnings.filterwarnings("ignore", category=sa_exc.SAWarning)  # sqlite has no native Decimal
    engine = make_engine(URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield make_sessionmaker(engine)
    await engine.dispose()


def snap(now: datetime) -> PendleMarketSnapshot:
    return PendleMarketSnapshot(
        chain_id=1,
        market="0xABC",
        name="sUSDe",
        protocol_name="Ethena",
        expiry=now + timedelta(days=30),
        is_active=True,
        pt="0xpt",
        yt="0xyt",
        sy="0xsy",
        underlying="0xu",
        accounting="0xa",
        implied_apy=Decimal("0.05"),
        underlying_apy=None,
        liquidity_usd=Decimal(1_000_000),
        total_tvl_usd=Decimal(2_000_000),
        total_pt=Decimal(10),
        total_sy=Decimal(10),
        fee_rate=None,
        categories=("stables",),
        observed_at=now,
        pt_usd=Decimal("0.99"),
        accounting_usd=Decimal(1),
    )


async def test_snapshots_and_latest(factory: async_sessionmaker[AsyncSession]) -> None:
    now = utcnow()
    async with session_scope(factory) as s:
        repo = SnapshotRepository(s)
        assert repo.add_pendle([snap(now)], whitelisted={"0xabc"}) == 1
        repo.add_gas(
            [GasSnapshot(Chain.ARBITRUM, 20_000_000, Decimal(3000), Decimal("0.001"))],
            now - timedelta(hours=1),
        )
        repo.add_gas([GasSnapshot(Chain.ARBITRUM, 30_000_000, Decimal(3100), Decimal("0.002"))], now)
        repo.add_pegs({"usde": Decimal("0.9991")}, now)
    async with session_scope(factory) as s:
        repo = SnapshotRepository(s)
        gas = await repo.latest_gas(timedelta(hours=2))
        assert gas[Chain.ARBITRUM].gas_price_wei == 30_000_000
        pegs = await repo.latest_pegs(timedelta(hours=2))
        assert abs(pegs["usde"] - Decimal("0.9991")) < Decimal("1e-9")
        assert await repo.latest_gas(timedelta(seconds=-1)) == {}


async def test_quotes_take_amm_only(factory: async_sessionmaker[AsyncSession]) -> None:
    now = utcnow()
    async with session_scope(factory) as s:
        repo = SnapshotRepository(s)
        common = {"effective_apy": None, "fee_usd": Decimal(0), "gas_used": None, "observed_at": now}
        repo.add_quote("k", Decimal(1000), use_limit_order=True, price_impact=Decimal("-0.0001"), **common)
        repo.add_quote("k", Decimal(1000), use_limit_order=False, price_impact=Decimal("-0.002"), **common)
    async with session_scope(factory) as s:
        quotes = await SnapshotRepository(s).latest_quotes(timedelta(hours=1))
    assert abs(quotes["k"][Decimal(1000)] - Decimal("0.002")) < Decimal("1e-9")


async def test_scan_and_decisions(factory: async_sessionmaker[AsyncSession]) -> None:
    now = utcnow()
    async with session_scope(factory) as s:
        repo = ScanRepository(s)
        scan_id = await repo.start(now)
        repo.add_decisions(
            scan_id,
            [Decision("k", DecisionAction.SKIP, "risk", threshold_apy=Decimal("Infinity"))],
            now,
        )
        await repo.finish(scan_id, status="ok", parking_apy=Decimal("0.04"), n_opps=1, n_eligible=0)
    async with session_scope(factory) as s:
        with pytest.raises(LookupError):
            await ScanRepository(s).finish(999, status="ok", parking_apy=None, n_opps=0, n_eligible=0)


async def test_history_dedup(factory: async_sessionmaker[AsyncSession]) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    points = [
        PendleHistoryPoint(
            now + timedelta(hours=i), Decimal("0.05"), Decimal("0.99"), None, None, None, None, None, None
        )
        for i in range(3)
    ]
    async with session_scope(factory) as s:
        repo = HistoryRepository(s)
        meta = await repo.upsert_market(snap(now))
        assert await repo.upsert_market(snap(now)) is meta
        assert await repo.add_points(1, "0xABC", points) == 3
    async with session_scope(factory) as s:
        repo = HistoryRepository(s)
        assert await repo.add_points(1, "0xabc", points) == 0
        assert len(await repo.points(1, "0xABC")) == 3
        assert len(await repo.markets()) == 1


async def test_monitor_events(factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_scope(factory) as s:
        MonitorRepository(s).add(
            [MonitorEvent("peg", Severity.WARNING, "USDe", "off peg 0.6%", Decimal("0.994"))]
        )
    async with session_scope(factory) as s:
        events = await MonitorRepository(s).unnotified()
    assert [e.subject for e in events] == ["USDe"]
