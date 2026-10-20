"""Repositories: map domain objects to rows and back. One class per aggregate."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ballast.costs.model import GasSnapshot
from ballast.domain.models import (
    Candidate,
    Chain,
    Decision,
    MonitorEvent,
    NlyResult,
    Opportunity,
    utcnow,
)
from ballast.sources.defillama import ParkingPool, ProtocolTvl
from ballast.sources.futures import BasisSnapshot
from ballast.sources.pendle import PendleHistoryPoint, PendleMarketSnapshot
from ballast.storage.models import (
    BasisSnapshotRow,
    DecisionRow,
    GasSnapshotRow,
    MonitorEventRow,
    NlyResultRow,
    PaperDailyRow,
    PaperPositionRow,
    ParkingSnapshotRow,
    PegPriceRow,
    PendleHistoryRow,
    PendleMarketMetaRow,
    PendleMarketSnapshotRow,
    ProtocolTvlRow,
    QuoteRow,
    RiskScoreRow,
    ScanRow,
)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass(slots=True)
class SnapshotRepository:
    session: AsyncSession

    def add_pendle(self, snaps: Iterable[PendleMarketSnapshot], whitelisted: set[str]) -> int:
        rows = [
            PendleMarketSnapshotRow(
                observed_at=s.observed_at,
                chain_id=s.chain_id,
                market=s.market.lower(),
                name=s.name,
                protocol_name=s.protocol_name,
                expiry=s.expiry,
                is_stable=s.is_stable,
                is_whitelisted=s.market.lower() in whitelisted,
                pt=s.pt,
                sy=s.sy,
                underlying=s.underlying,
                accounting=s.accounting,
                implied_apy=s.implied_apy,
                underlying_apy=s.underlying_apy,
                liquidity_usd=s.liquidity_usd,
                total_tvl_usd=s.total_tvl_usd,
                total_pt=s.total_pt,
                total_sy=s.total_sy,
                fee_rate=s.fee_rate,
                pt_usd=s.pt_usd,
                accounting_usd=s.accounting_usd,
                underlying_usd=s.underlying_usd,
                categories=list(s.categories),
            )
            for s in snaps
        ]
        self.session.add_all(rows)
        return len(rows)

    def add_parking(self, pools: Iterable[ParkingPool]) -> None:
        self.session.add_all(
            ParkingSnapshotRow(
                observed_at=p.observed_at,
                pool_id=p.pool_id,
                protocol=p.protocol,
                chain_id=int(p.chain),
                asset_symbol=p.asset_symbol,
                apy_base=p.apy_base,
                apy_reward=p.apy_reward,
                tvl_usd=p.tvl_usd,
            )
            for p in pools
        )

    def add_basis(self, snaps: Iterable[BasisSnapshot]) -> None:
        self.session.add_all(
            BasisSnapshotRow(
                observed_at=s.observed_at,
                venue=s.venue,
                instrument=s.instrument,
                underlying=s.underlying,
                expiry=s.expiry,
                future_price=s.future_price,
                index_price=s.index_price,
                basis_apr=s.basis_apr,
            )
            for s in snaps
        )

    def add_gas(self, snaps: Iterable[GasSnapshot], observed_at: datetime) -> None:
        self.session.add_all(
            GasSnapshotRow(
                observed_at=observed_at,
                chain_id=int(s.chain),
                gas_price_wei=Decimal(s.gas_price_wei),
                native_usd=s.native_usd,
                l1_fee_usd=s.l1_fee_usd_per_tx,
            )
            for s in snaps
        )

    def add_pegs(self, prices: dict[str, Decimal], observed_at: datetime) -> None:
        self.session.add_all(
            PegPriceRow(observed_at=observed_at, symbol=k, price_usd=v) for k, v in prices.items()
        )

    def add_tvl(self, protocol: str, tvl: ProtocolTvl) -> None:
        self.session.add(
            ProtocolTvlRow(
                observed_at=tvl.observed_at,
                protocol=protocol,
                llama_slug=tvl.slug,
                tvl_usd=tvl.tvl_usd,
                tvl_7d_ago_usd=tvl.tvl_7d_ago_usd,
            )
        )

    def add_quote(
        self,
        opportunity_key: str,
        size_usd: Decimal,
        *,
        use_limit_order: bool,
        price_impact: Decimal,
        effective_apy: Decimal | None,
        fee_usd: Decimal,
        gas_used: int | None,
        observed_at: datetime,
    ) -> None:
        self.session.add(
            QuoteRow(
                observed_at=observed_at,
                opportunity_key=opportunity_key,
                size_usd=size_usd,
                use_limit_order=use_limit_order,
                price_impact=price_impact,
                effective_apy=effective_apy,
                fee_usd=fee_usd,
                gas_used=gas_used,
            )
        )

    async def previous_market_apy(self) -> dict[str, Decimal]:
        """Latest stored implied APY per portfolio-chain market, keyed like ``Opportunity.key``."""
        latest = (
            select(
                PendleMarketSnapshotRow.chain_id, func.max(PendleMarketSnapshotRow.observed_at).label("at")
            )
            .group_by(PendleMarketSnapshotRow.chain_id)
            .subquery()
        )
        rows = await self.session.execute(
            select(
                PendleMarketSnapshotRow.chain_id,
                PendleMarketSnapshotRow.market,
                PendleMarketSnapshotRow.implied_apy,
            ).join(
                latest,
                (PendleMarketSnapshotRow.chain_id == latest.c.chain_id)
                & (PendleMarketSnapshotRow.observed_at == latest.c.at),
            )
        )
        out: dict[str, Decimal] = {}
        for chain_id, market, apy in rows.all():
            if apy is None or chain_id not in {int(c) for c in Chain}:
                continue
            out[f"pendle:{Chain(chain_id).slug}:{market.lower()}"] = apy
        return out

    async def previous_parking_apy(self) -> dict[str, Decimal]:
        latest = select(func.max(ParkingSnapshotRow.observed_at)).scalar_subquery()
        rows = await self.session.scalars(
            select(ParkingSnapshotRow).where(ParkingSnapshotRow.observed_at == latest)
        )
        return {
            f"parking:{r.protocol}:{Chain(r.chain_id).slug}:{r.asset_symbol}:{r.pool_id[:8]}": r.apy_base
            for r in rows.all()
        }

    async def latest_gas(self, max_age: timedelta) -> dict[Chain, GasSnapshot]:
        cutoff = utcnow() - max_age
        rows = (
            await self.session.scalars(
                select(GasSnapshotRow)
                .where(GasSnapshotRow.observed_at >= cutoff)
                .order_by(GasSnapshotRow.observed_at.desc())
            )
        ).all()
        out: dict[Chain, GasSnapshot] = {}
        for row in rows:
            chain = Chain(row.chain_id)
            if chain not in out:
                out[chain] = GasSnapshot(chain, int(row.gas_price_wei), row.native_usd, row.l1_fee_usd)
        return out

    async def latest_pegs(self, max_age: timedelta) -> dict[str, Decimal]:
        cutoff = utcnow() - max_age
        rows = (
            await self.session.scalars(
                select(PegPriceRow)
                .where(PegPriceRow.observed_at >= cutoff)
                .order_by(PegPriceRow.observed_at.desc())
            )
        ).all()
        out: dict[str, Decimal] = {}
        for row in rows:
            out.setdefault(row.symbol, row.price_usd)
        return out

    async def latest_tvl(self, max_age: timedelta) -> dict[str, ProtocolTvlRow]:
        cutoff = utcnow() - max_age
        rows = (
            await self.session.scalars(
                select(ProtocolTvlRow)
                .where(ProtocolTvlRow.observed_at >= cutoff)
                .order_by(ProtocolTvlRow.observed_at.desc())
            )
        ).all()
        out: dict[str, ProtocolTvlRow] = {}
        for row in rows:
            out.setdefault(row.protocol, row)
        return out

    async def latest_quotes(self, max_age: timedelta) -> dict[str, dict[Decimal, Decimal]]:
        """AMM-only quotes (no limit orders): ``{key: {size: slippage fraction}}``; conservative."""
        cutoff = utcnow() - max_age
        rows = (
            await self.session.scalars(
                select(QuoteRow)
                .where(QuoteRow.observed_at >= cutoff, QuoteRow.use_limit_order.is_(False))
                .order_by(QuoteRow.observed_at.desc())
            )
        ).all()
        out: dict[str, dict[Decimal, Decimal]] = {}
        for row in rows:
            by_size = out.setdefault(row.opportunity_key, {})
            by_size.setdefault(row.size_usd, max(-row.price_impact, Decimal(0)))
        return out


@dataclass(slots=True)
class ScanRepository:
    session: AsyncSession

    async def start(self, started_at: datetime) -> int:
        row = ScanRow(started_at=started_at, status="running")
        self.session.add(row)
        await self.session.flush()
        return row.id

    async def finish(
        self,
        scan_id: int,
        *,
        status: str,
        parking_apy: Decimal | None,
        n_opps: int,
        n_eligible: int,
        notes: str = "",
    ) -> None:
        row = await self.session.get(ScanRow, scan_id)
        if row is None:
            msg = f"scan {scan_id} not found"
            raise LookupError(msg)
        row.finished_at = utcnow()
        row.status = status
        row.parking_apy = parking_apy
        row.n_opportunities = n_opps
        row.n_eligible = n_eligible
        row.notes = notes

    def add_candidate(self, scan_id: int, cand: Candidate) -> None:
        opp, nly = cand.opportunity, cand.nly
        self.session.add(_nly_row(scan_id, opp, nly))
        self.session.add(
            RiskScoreRow(
                scan_id=scan_id,
                opportunity_key=opp.key,
                tier=cand.risk.tier.value,
                score=cand.risk.score,
                allowed=cand.risk.allowed,
                threshold_apy=cand.threshold_apy if cand.threshold_apy.is_finite() else None,
                findings={
                    f.rule: {"score": str(f.score), "reason": f.reason, "blocking": f.blocking}
                    for f in cand.risk.findings
                },
            )
        )

    def add_decisions(self, scan_id: int, decisions: Sequence[Decision], at: datetime) -> None:
        self.session.add_all(
            DecisionRow(
                scan_id=scan_id,
                created_at=at,
                opportunity_key=d.opportunity_key,
                action=d.action.value,
                reason=d.reason,
                capital_usd=d.capital_usd,
                nly_apy=d.nly_apy,
                threshold_apy=d.threshold_apy
                if d.threshold_apy is None or d.threshold_apy.is_finite()
                else None,
            )
            for d in decisions
        )


def _nly_row(scan_id: int, opp: Opportunity, nly: NlyResult) -> NlyResultRow:
    return NlyResultRow(
        scan_id=scan_id,
        opportunity_key=opp.key,
        chain_id=int(opp.chain),
        kind=opp.kind.value,
        asset_symbol=opp.asset_symbol,
        protocol=opp.protocol,
        maturity=opp.maturity,
        quoted_apy=opp.quoted_apy,
        capital_usd=nly.capital_usd,
        days=nly.days,
        gross_return=nly.gross_return,
        net_return=nly.net_return,
        nly_apy=nly.nly_apy,
        nly_monthly=nly.nly_monthly,
        gas_usd=nly.costs.gas_usd,
        fees_usd=nly.costs.fees_usd,
        slippage_usd=nly.costs.slippage_usd,
        liquidity_usd=opp.liquidity_usd,
    )


@dataclass(slots=True)
class MonitorRepository:
    session: AsyncSession

    def add(self, events: Iterable[MonitorEvent]) -> None:
        self.session.add_all(
            MonitorEventRow(
                observed_at=e.observed_at,
                kind=e.kind,
                severity=e.severity.value,
                subject=e.subject,
                message=e.message,
                value=e.value,
            )
            for e in events
        )

    async def recent(self, since: datetime) -> list[tuple[str, str, str, datetime]]:
        rows = await self.session.execute(
            select(
                MonitorEventRow.kind,
                MonitorEventRow.subject,
                MonitorEventRow.severity,
                MonitorEventRow.observed_at,
            ).where(MonitorEventRow.observed_at >= since)
        )
        return [(k, s, sev, _utc(at)) for k, s, sev, at in rows.all()]

    async def mark_notified(self, ids: Iterable[int]) -> None:
        for event_id in ids:
            row = await self.session.get(MonitorEventRow, event_id)
            if row is not None:
                row.notified = True

    async def unnotified(self, limit: int = 50) -> list[MonitorEventRow]:
        rows = await self.session.scalars(
            select(MonitorEventRow)
            .where(MonitorEventRow.notified.is_(False))
            .order_by(MonitorEventRow.observed_at)
            .limit(limit)
        )
        return list(rows.all())


@dataclass(slots=True)
class HistoryRepository:
    session: AsyncSession

    async def upsert_market(self, snap: PendleMarketSnapshot) -> PendleMarketMetaRow:
        row = await self.session.scalar(
            select(PendleMarketMetaRow).where(
                PendleMarketMetaRow.chain_id == snap.chain_id,
                PendleMarketMetaRow.market == snap.market.lower(),
            )
        )
        if row is None:
            row = PendleMarketMetaRow(
                chain_id=snap.chain_id,
                market=snap.market.lower(),
                name=snap.name,
                protocol_name=snap.protocol_name,
                expiry=snap.expiry,
                is_stable=snap.is_stable,
                pt=snap.pt,
                sy=snap.sy,
                underlying=snap.underlying,
                accounting=snap.accounting,
                fee_rate=snap.fee_rate,
                history_loaded_at=None,
            )
            self.session.add(row)
            await self.session.flush()
        return row

    async def add_points(self, chain_id: int, market: str, points: Iterable[PendleHistoryPoint]) -> int:
        existing = set(
            (
                await self.session.scalars(
                    select(PendleHistoryRow.timestamp).where(
                        PendleHistoryRow.chain_id == chain_id, PendleHistoryRow.market == market.lower()
                    )
                )
            ).all()
        )
        existing_naive = {ts.replace(tzinfo=None) for ts in existing}
        new = [p for p in points if p.timestamp.replace(tzinfo=None) not in existing_naive]
        self.session.add_all(
            PendleHistoryRow(
                chain_id=chain_id,
                market=market.lower(),
                timestamp=p.timestamp,
                implied_apy=p.implied_apy,
                pt_usd=p.pt_usd,
                sy_usd=p.sy_usd,
                tvl_usd=p.tvl_usd,
                total_pt=p.total_pt,
                total_sy=p.total_sy,
                underlying_apy=p.underlying_apy,
                volume_usd=p.volume_usd,
            )
            for p in new
        )
        return len(new)

    async def markets(self, *, stable_only: bool = True) -> list[PendleMarketMetaRow]:
        stmt = select(PendleMarketMetaRow)
        if stable_only:
            stmt = stmt.where(PendleMarketMetaRow.is_stable.is_(True))
        return list((await self.session.scalars(stmt.order_by(PendleMarketMetaRow.expiry))).all())

    async def points(self, chain_id: int, market: str) -> list[PendleHistoryRow]:
        rows = await self.session.scalars(
            select(PendleHistoryRow)
            .where(PendleHistoryRow.chain_id == chain_id, PendleHistoryRow.market == market.lower())
            .order_by(PendleHistoryRow.timestamp)
        )
        return list(rows.all())

    async def count_points(self) -> int:
        return int(await self.session.scalar(select(func.count(PendleHistoryRow.id))) or 0)


@dataclass(slots=True)
class PaperRepository:
    session: AsyncSession

    async def open_positions(self, portfolio: str) -> list[PaperPositionRow]:
        rows = await self.session.scalars(
            select(PaperPositionRow).where(
                PaperPositionRow.portfolio == portfolio, PaperPositionRow.status == "open"
            )
        )
        return list(rows.all())

    async def all_positions(self, portfolio: str) -> list[PaperPositionRow]:
        rows = await self.session.scalars(
            select(PaperPositionRow)
            .where(PaperPositionRow.portfolio == portfolio)
            .order_by(PaperPositionRow.opened_at)
        )
        return list(rows.all())

    def add_position(self, row: PaperPositionRow) -> None:
        self.session.add(row)

    async def upsert_daily(self, row: PaperDailyRow) -> None:
        existing = await self.session.scalar(
            select(PaperDailyRow).where(
                PaperDailyRow.portfolio == row.portfolio, PaperDailyRow.day == row.day
            )
        )
        if existing is None:
            self.session.add(row)
            return
        for column in (
            "total_value_usd",
            "locked_usd",
            "parked_usd",
            "idle_usd",
            "accrued_usd",
            "costs_paid_usd",
            "parking_apy",
            "n_positions",
        ):
            setattr(existing, column, getattr(row, column))

    async def daily(self, portfolio: str) -> list[PaperDailyRow]:
        rows = await self.session.scalars(
            select(PaperDailyRow).where(PaperDailyRow.portfolio == portfolio).order_by(PaperDailyRow.day)
        )
        return list(rows.all())
