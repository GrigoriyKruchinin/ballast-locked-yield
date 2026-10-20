"""Persist :class:`PaperState` (accounts + positions) and daily snapshots."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ballast.domain.models import Chain, SourceKind
from ballast.portfolio.paper import ClosedPosition, LockedPosition, PaperState, ParkingBalance
from ballast.storage.models import PaperAccountRow, PaperDailyRow, PaperPositionRow
from ballast.storage.repositories import PaperRepository

OPEN = "open"


def _aware(value: datetime) -> datetime:
    """SQLite drops tzinfo; all stored times are UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


CLOSED = "closed"


def _locked_row(name: str, p: LockedPosition, status: str) -> PaperPositionRow:
    return PaperPositionRow(
        portfolio=name,
        opportunity_key=p.key,
        kind=p.kind.value,
        chain_id=int(p.chain),
        asset_symbol=p.asset_symbol,
        protocol=p.protocol,
        status=status,
        capital_usd=p.capital_usd,
        entry_price=p.entry_price,
        units=p.units,
        expected_apy=p.expected_apy,
        payoff_per_unit=p.payoff_per_unit,
        expected_payoff_usd=p.expected_payoff_usd,
        entry_costs_usd=p.entry_costs_usd,
        expected_exit_costs_usd=p.expected_exit_costs_usd,
        exposures=sorted(p.exposures),
        maturity=p.maturity,
        opened_at=p.opened_at,
        closed_at=None,
        payoff_usd=None,
        close_reason=None,
    )


def _parking_row(name: str, b: ParkingBalance, state: PaperState) -> PaperPositionRow:
    return PaperPositionRow(
        portfolio=name,
        opportunity_key=b.key,
        kind=SourceKind.PARKING.value,
        chain_id=int(b.chain),
        asset_symbol=b.key.split(":")[3] if b.key.count(":") >= 3 else "",
        protocol=b.protocol,
        status=OPEN,
        capital_usd=b.balance_usd,
        entry_price=Decimal(1),
        units=b.balance_usd,
        expected_apy=b.apy,
        payoff_per_unit=None,
        expected_payoff_usd=None,
        entry_costs_usd=Decimal(0),
        expected_exit_costs_usd=Decimal(0),
        exposures=sorted(b.exposures),
        maturity=None,
        opened_at=state.updated_at,
        closed_at=None,
        payoff_usd=None,
        close_reason=None,
    )


def _locked_from_row(row: PaperPositionRow) -> LockedPosition:
    if row.maturity is None or row.expected_payoff_usd is None:
        msg = f"position {row.id} lacks maturity/expected payoff"
        raise ValueError(msg)
    return LockedPosition(
        key=row.opportunity_key,
        kind=SourceKind(row.kind),
        chain=Chain(row.chain_id),
        asset_symbol=row.asset_symbol,
        protocol=row.protocol,
        capital_usd=row.capital_usd,
        units=row.units,
        entry_price=row.entry_price,
        payoff_per_unit=row.payoff_per_unit or Decimal(1),
        expected_apy=row.expected_apy,
        expected_payoff_usd=row.expected_payoff_usd,
        entry_costs_usd=row.entry_costs_usd,
        expected_exit_costs_usd=row.expected_exit_costs_usd,
        exposures=frozenset(row.exposures),
        opened_at=_aware(row.opened_at),
        maturity=_aware(row.maturity),
    )


async def load_state(session: AsyncSession, name: str) -> PaperState | None:
    account = await session.get(PaperAccountRow, name)
    if account is None:
        return None
    rows = await PaperRepository(session).open_positions(name)
    state = PaperState(
        name=name,
        initial_capital_usd=account.initial_capital_usd,
        started_at=_aware(account.started_at),
        updated_at=_aware(account.updated_at),
        idle_usd=account.idle_usd,
        costs_paid_usd=account.costs_paid_usd,
    )
    for row in rows:
        if row.kind == SourceKind.PARKING.value:
            state.parking[row.opportunity_key] = ParkingBalance(
                key=row.opportunity_key,
                chain=Chain(row.chain_id),
                protocol=row.protocol,
                balance_usd=row.capital_usd,
                apy=row.expected_apy,
                exposures=frozenset(row.exposures),
            )
        else:
            state.locked.append(_locked_from_row(row))
    return state


async def save_state(session: AsyncSession, state: PaperState, chains: Sequence[Chain]) -> None:
    account = await session.get(PaperAccountRow, state.name)
    if account is None:
        account = PaperAccountRow(
            name=state.name,
            initial_capital_usd=state.initial_capital_usd,
            started_at=state.started_at,
            updated_at=state.updated_at,
            idle_usd=state.idle_usd,
            costs_paid_usd=state.costs_paid_usd,
            chains=[c.slug for c in chains],
        )
        session.add(account)
    else:
        account.updated_at = state.updated_at
        account.idle_usd = state.idle_usd
        account.costs_paid_usd = state.costs_paid_usd
    await session.execute(
        delete(PaperPositionRow).where(
            PaperPositionRow.portfolio == state.name, PaperPositionRow.status == OPEN
        )
    )
    session.add_all(_locked_row(state.name, p, OPEN) for p in state.locked)
    session.add_all(_parking_row(state.name, b, state) for b in state.parking.values())
    session.add_all(_closed_row(state.name, c) for c in state.closed)
    state.closed.clear()


def _closed_row(name: str, closed: ClosedPosition) -> PaperPositionRow:
    row = _locked_row(name, closed.position, CLOSED)
    row.closed_at = closed.closed_at
    row.payoff_usd = closed.payoff_usd
    row.close_reason = closed.reason
    return row


async def save_daily(
    session: AsyncSession, state: PaperState, day: date, accrued_usd: Decimal, parking_apy: Decimal
) -> None:
    now = state.updated_at
    await PaperRepository(session).upsert_daily(
        PaperDailyRow(
            portfolio=state.name,
            day=day,
            total_value_usd=state.total_value(now),
            locked_usd=state.locked_value(now),
            parked_usd=state.parked_value(),
            idle_usd=state.idle_usd,
            accrued_usd=accrued_usd,
            costs_paid_usd=state.costs_paid_usd,
            parking_apy=parking_apy,
            n_positions=len(state.locked),
        )
    )


async def closed_positions(session: AsyncSession, name: str) -> list[PaperPositionRow]:
    rows = await session.scalars(
        select(PaperPositionRow).where(PaperPositionRow.portfolio == name, PaperPositionRow.status == CLOSED)
    )
    return list(rows.all())
