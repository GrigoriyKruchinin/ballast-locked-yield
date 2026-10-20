"""Backfill hourly Pendle history for the backtest (matured and active stable markets)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ballast.domain.models import Chain, utcnow
from ballast.logging import get_logger
from ballast.sources.http import SourceUnavailableError
from ballast.sources.pendle import PendleClient
from ballast.storage.db import session_scope
from ballast.storage.models import PendleMarketMetaRow
from ballast.storage.repositories import HistoryRepository

log = get_logger(__name__)

WINDOW = timedelta(days=59)  # 1440 hourly points max per call
LOOKBACK = timedelta(days=548)  # ~18 months of matured markets
MAX_MARKET_LIFE = timedelta(days=400)


@dataclass(frozen=True, slots=True)
class BackfillReport:
    markets_seen: int
    markets_loaded: int
    points_added: int
    failures: int


async def backfill_history(
    pendle: PendleClient,
    sessions: async_sessionmaker[AsyncSession],
    chains: Sequence[Chain],
    *,
    now: datetime | None = None,
    max_markets: int | None = None,
) -> BackfillReport:
    at = now or utcnow()
    seen = loaded = added = failures = 0
    for chain in chains:
        try:
            markets = await pendle.markets(int(chain), active=False) + await pendle.markets(
                int(chain), active=True
            )
        except SourceUnavailableError as exc:
            log.warning("history_markets_failed", chain=chain.slug, error=str(exc))
            failures += 1
            continue
        for snap in markets:
            if not snap.is_stable or snap.expiry < at - LOOKBACK:
                continue
            seen += 1
            async with session_scope(sessions) as session:
                meta = await HistoryRepository(session).upsert_market(snap)
                done = meta.history_loaded_at is not None and snap.expiry < at
            if done:
                continue
            if max_markets is not None and loaded >= max_markets:
                continue
            try:
                added += await _load_market(pendle, sessions, meta, at)
                loaded += 1
            except SourceUnavailableError as exc:
                log.warning("history_market_failed", market=snap.market, error=str(exc))
                failures += 1
    return BackfillReport(seen, loaded, added, failures)


async def _load_market(
    pendle: PendleClient, sessions: async_sessionmaker[AsyncSession], meta: PendleMarketMetaRow, now: datetime
) -> int:
    expiry = meta.expiry if meta.expiry.tzinfo else meta.expiry.replace(tzinfo=now.tzinfo)
    end = min(expiry, now)
    start = end - MAX_MARKET_LIFE
    added = 0
    cursor = start
    while cursor < end:
        window_end = min(cursor + WINDOW, end)
        points = await pendle.history(meta.chain_id, meta.market, cursor, window_end)
        async with session_scope(sessions) as session:
            added += await HistoryRepository(session).add_points(meta.chain_id, meta.market, points)
        cursor = window_end
    async with session_scope(sessions) as session:
        row = await session.get(PendleMarketMetaRow, meta.id)
        if row is not None:
            row.history_loaded_at = now
    return added
