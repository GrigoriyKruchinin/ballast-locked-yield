"""Notification flows: per-scan alerts, daily and weekly digests."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ballast.domain.models import Chain, MonitorEvent, Severity, utcnow
from ballast.logging import get_logger
from ballast.notify.formatting import OpportunityView, format_daily, format_events, format_opportunities
from ballast.notify.telegram import TelegramClient, TelegramError
from ballast.pipeline.run import BookSummary
from ballast.portfolio.store import load_state
from ballast.storage.db import session_scope
from ballast.storage.models import NlyResultRow, PaperDailyRow, RiskScoreRow, ScanRow
from ballast.storage.repositories import MonitorRepository, PaperRepository

log = get_logger(__name__)


async def _views_for(session: AsyncSession, keys: set[str]) -> list[OpportunityView]:
    views: list[OpportunityView] = []
    for key in sorted(keys):
        nly = await session.scalar(
            select(NlyResultRow)
            .where(NlyResultRow.opportunity_key == key)
            .order_by(NlyResultRow.id.desc())
            .limit(1)
        )
        risk = await session.scalar(
            select(RiskScoreRow)
            .where(RiskScoreRow.opportunity_key == key)
            .order_by(RiskScoreRow.id.desc())
            .limit(1)
        )
        if nly is None or risk is None:
            continue
        views.append(
            OpportunityView(
                asset_symbol=nly.asset_symbol,
                chain=Chain(nly.chain_id).slug,
                tier=risk.tier,
                nly_apy=nly.nly_apy,
                nly_monthly=nly.nly_monthly,
                threshold_apy=risk.threshold_apy,
                days=nly.days,
                risk_score=risk.score,
                gas_usd=nly.gas_usd,
                fees_usd=nly.fees_usd,
                slippage_usd=nly.slippage_usd,
            )
        )
    return views


async def notify_events(telegram: TelegramClient, sessions: async_sessionmaker[AsyncSession]) -> int:
    """Send unnotified monitor events (opportunities and risk) from the DB, then mark them notified."""
    async with session_scope(sessions) as session:
        rows = await MonitorRepository(session).unnotified(limit=200)
        opportunity_keys = {r.subject for r in rows if r.kind == "opportunity"}
        views = await _views_for(session, opportunity_keys)
        parking_apy = await session.scalar(select(ScanRow.parking_apy).order_by(ScanRow.id.desc()).limit(1))
    risk_rows = [r for r in rows if r.kind != "opportunity" and r.severity != Severity.INFO.value]
    events = [
        MonitorEvent(r.kind, Severity(r.severity), r.subject, r.message, r.value, r.observed_at)
        for r in risk_rows
    ]
    sent = 0
    try:
        if views:
            sent += await telegram.send(format_opportunities(views, parking_apy))
        if events:
            sent += await telegram.send(
                format_events(events), silent=all(e.severity is Severity.WARNING for e in events)
            )
    except TelegramError as exc:
        log.warning("telegram_failed", error=str(exc))
        return sent
    async with session_scope(sessions) as session:
        await MonitorRepository(session).mark_notified(r.id for r in rows)
    return sent


def _book_summary(row: PaperDailyRow) -> BookSummary:
    return BookSummary(
        book=row.portfolio,
        total_value_usd=row.total_value_usd,
        locked_usd=row.locked_usd,
        parked_usd=row.parked_usd,
        idle_usd=row.idle_usd,
        parking_apy=row.parking_apy or Decimal(0),
    )


async def _digest_rows(
    session: AsyncSession, book: str, period_days: int, today: date
) -> tuple[PaperDailyRow, Decimal, Decimal] | None:
    rows = await PaperRepository(session).daily(book)
    if not rows:
        return None
    latest = rows[-1]
    state = await load_state(session, book)
    initial = state.initial_capital_usd if state else rows[0].total_value_usd
    cutoff = today - timedelta(days=period_days)
    base_rows = [r for r in rows if r.day <= cutoff]
    base = base_rows[-1].total_value_usd if base_rows else initial
    since_start = latest.total_value_usd / initial - 1 if initial else Decimal(0)
    period = latest.total_value_usd / base - 1 if base else Decimal(0)
    return latest, since_start, period


async def send_digest(
    telegram: TelegramClient,
    sessions: async_sessionmaker[AsyncSession],
    books: list[str],
    *,
    weekly: bool,
    now: datetime | None = None,
) -> int:
    at = now or utcnow()
    period_days = 7 if weekly else 1
    entries: list[tuple[BookSummary, Decimal, Decimal]] = []
    upcoming: list[tuple[str, datetime]] = []
    async with session_scope(sessions) as session:
        for book in books:
            result = await _digest_rows(session, book, period_days, at.date())
            if result is None:
                continue
            row, since_start, period = result
            entries.append((_book_summary(row), since_start, period))
            for pos in await PaperRepository(session).open_positions(book):
                if pos.maturity is not None and pos.kind != "parking":
                    upcoming.append((f"{pos.asset_symbol} ({book})", pos.maturity))
    if not entries:
        return 0
    title = "📈 <b>Итоги недели</b>" if weekly else "📊 <b>Итоги дня</b>"
    try:
        return await telegram.send(format_daily(entries, sorted(upcoming, key=lambda x: x[1]), at, title))
    except TelegramError as exc:
        log.warning("telegram_failed", error=str(exc))
        return 0


__all__ = ["notify_events", "send_digest"]
