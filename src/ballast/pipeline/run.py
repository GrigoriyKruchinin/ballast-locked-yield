"""One full scan: collect -> persist -> analyze per book -> paper step -> persist decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from ballast.costs.model import GasSnapshot
from ballast.domain.models import (
    Candidate,
    Chain,
    Decision,
    DecisionAction,
    MonitorEvent,
    Opportunity,
    Severity,
    utcnow,
)
from ballast.logging import get_logger
from ballast.monitor.monitors import THROTTLE, MonitorInputs, RiskMonitor, throttle
from ballast.nly.calculator import NlyCalculator
from ballast.pipeline.analyze import AnalysisInputs, AnalysisResult
from ballast.pipeline.collect import TVL_REFRESH, CollectedData, persist
from ballast.pipeline.context import AppContext, Book
from ballast.portfolio.paper import LockedPosition, PaperEngine, PaperState
from ballast.portfolio.store import load_state, save_daily, save_state
from ballast.risk.rules import MarketState
from ballast.storage.db import session_scope
from ballast.storage.repositories import MonitorRepository, ScanRepository, SnapshotRepository

log = get_logger(__name__)

GAS_FALLBACK_AGE = timedelta(hours=1)
PEG_FALLBACK_AGE = timedelta(hours=2)
TVL_MAX_AGE = timedelta(hours=48)
QUOTE_MAX_AGE = timedelta(hours=3)
PARKING_GAS_FALLBACK_USD = Decimal("0.5")


@dataclass(slots=True)
class BookSummary:
    book: str
    total_value_usd: Decimal
    locked_usd: Decimal
    parked_usd: Decimal
    idle_usd: Decimal
    parking_apy: Decimal
    opened: list[LockedPosition] = field(default_factory=list)
    matured: int = 0
    eligible: list[Candidate] = field(default_factory=list)


@dataclass(slots=True)
class ScanSummary:
    errors: list[str]
    n_markets: int
    n_whitelisted: int
    books: list[BookSummary]
    events: list[MonitorEvent] = field(default_factory=list)


async def _market_inputs(ctx: AppContext, data: CollectedData) -> AnalysisInputs:
    async with session_scope(ctx.sessions) as session:
        repo = SnapshotRepository(session)
        gas: dict[Chain, GasSnapshot] = dict(await repo.latest_gas(GAS_FALLBACK_AGE))
        gas.update(data.gas)
        pegs = dict(await repo.latest_pegs(PEG_FALLBACK_AGE))
        pegs.update(data.pegs)
        tvl_rows = await repo.latest_tvl(TVL_MAX_AGE)
        quotes = await repo.latest_quotes(QUOTE_MAX_AGE)
    tvl = {p: r.tvl_usd for p, r in tvl_rows.items()}
    change = {p: r.tvl_usd / r.tvl_7d_ago_usd - 1 for p, r in tvl_rows.items() if r.tvl_7d_ago_usd}
    for protocol, fresh in data.tvl.items():
        tvl[protocol] = fresh.tvl_usd
        if fresh.change_7d is not None:
            change[protocol] = fresh.change_7d
    state = MarketState(
        as_of=data.observed_at.date(),
        protocol_tvl_usd=tvl,
        protocol_tvl_change_7d=change,
        asset_usd_price=pegs,
    )
    return AnalysisInputs(data.opportunities, data.parking, gas, state, quotes)


def _paper_engine(ctx: AppContext, inputs: AnalysisInputs) -> PaperEngine:
    cost_model = ctx.analyzer.cost_model(inputs)
    calc = NlyCalculator(cost_model)
    parking_units = cost_model.parking_gas_units

    def exit_cost(chain: Chain) -> Decimal:
        snap = inputs.gas.get(chain)
        return snap.tx_cost_usd(parking_units) if snap else PARKING_GAS_FALLBACK_USD

    return PaperEngine(
        evaluate=lambda opp, size: calc.evaluate(opp, size, opp.observed_at),
        parking_exit_cost=exit_cost,
    )


async def _run_book(
    ctx: AppContext, book: Book, data: CollectedData, inputs: AnalysisInputs
) -> tuple[BookSummary, AnalysisResult]:
    alloc = ctx.settings.allocation
    if alloc is None:
        msg = "allocation settings missing"
        raise RuntimeError(msg)
    now = data.observed_at
    async with session_scope(ctx.sessions) as session:
        state = await load_state(session, book.name) or PaperState.new(
            book.name, alloc.paper_capital_usd, now
        )
    parking_rates = {p.key: p.quoted_apy for p in data.parking if p.quoted_apy is not None}
    peg_of = {a.symbol.lower(): a.peg_symbol for a in ctx.whitelist.assets}

    engine = _paper_engine(ctx, inputs)
    accrued, matured, _ = engine.accrue_and_mature(
        state, now, parking_rates, inputs.state.asset_usd_price, lambda s: peg_of.get(s.lower(), s)
    )
    analysis = ctx.analyzer.run(
        inputs,
        capital_usd=state.total_value(now),
        now=now,
        chains=book.chains,
        existing=state.existing_allocations(),
    )
    opportunities: dict[str, Opportunity] = {c.opportunity.key: c.opportunity for c in analysis.candidates}
    opened, _, _ = engine.apply_plan(state, analysis.plan, opportunities, now)

    async with session_scope(ctx.sessions) as session:
        scans = ScanRepository(session)
        scan_id = await scans.start(now)
        for cand in (*analysis.candidates, *analysis.parking):
            scans.add_candidate(scan_id, cand)
        decisions: list[Decision] = [*analysis.plan.decisions, *analysis.errors]
        scans.add_decisions(scan_id, decisions, now)
        await scans.finish(
            scan_id,
            status="ok" if not data.errors else "partial",
            parking_apy=analysis.parking_apy,
            n_opps=len(analysis.candidates),
            n_eligible=len(analysis.eligible),
            notes=f"book={book.name}; errors={len(data.errors)}",
        )
        await save_state(session, state, book.chains)
        await save_daily(session, state, now.date(), accrued, analysis.parking_apy)

    summary = BookSummary(
        book=book.name,
        total_value_usd=state.total_value(now),
        locked_usd=state.locked_value(now),
        parked_usd=state.parked_value(),
        idle_usd=state.idle_usd,
        parking_apy=analysis.parking_apy,
        opened=opened,
        matured=len(matured),
        eligible=analysis.eligible,
    )
    return summary, analysis


async def run_scan(ctx: AppContext) -> ScanSummary:
    async with session_scope(ctx.sessions) as session:
        snapshots = SnapshotRepository(session)
        latest_tvl = await snapshots.latest_tvl(TVL_REFRESH)
        previous_apy = await snapshots.previous_market_apy()
        previous_parking = await snapshots.previous_parking_apy()
        recent = await MonitorRepository(session).recent(utcnow() - THROTTLE)
    data = await ctx.collector.collect(include_tvl=not latest_tvl)
    async with session_scope(ctx.sessions) as session:
        persist(data, SnapshotRepository(session), ctx.whitelist)
    inputs = await _market_inputs(ctx, data)
    summaries: list[BookSummary] = []
    for book in ctx.books:
        summary, _ = await _run_book(ctx, book, data, inputs)
        summaries.append(summary)
    events = _monitor(ctx, data, previous_apy, previous_parking)
    for summary in summaries:
        events += [
            MonitorEvent(
                "opportunity",
                Severity.INFO,
                c.opportunity.key,
                f"{summary.book}",
                c.nly.nly_apy,
                data.observed_at,
            )
            for c in summary.eligible
        ]
    events = throttle(events, recent)
    async with session_scope(ctx.sessions) as session:
        MonitorRepository(session).add(events)
    n_wl = sum(
        1
        for o in data.opportunities
        if ctx.whitelist.find_asset(o.asset_symbol, o.chain, o.asset_address) is not None
    )
    log.info(
        "scan_done",
        markets=len(data.pendle),
        whitelisted=n_wl,
        errors=len(data.errors),
        books={s.book: str(round(s.total_value_usd, 2)) for s in summaries},
    )
    return ScanSummary(
        errors=data.errors, n_markets=len(data.pendle), n_whitelisted=n_wl, books=summaries, events=events
    )


def _monitor(
    ctx: AppContext,
    data: CollectedData,
    previous_apy: dict[str, Decimal],
    previous_parking: dict[str, Decimal],
) -> list[MonitorEvent]:
    risk = ctx.settings.risk
    if risk is None:
        return []
    inputs = MonitorInputs(
        observed_at=data.observed_at,
        pegs=data.pegs,
        tvl=data.tvl,
        opportunities=data.opportunities,
        previous_apy=previous_apy,
        parking=data.parking,
        previous_parking_apy=previous_parking,
        errors=data.errors,
    )
    return RiskMonitor(ctx.whitelist, risk).evaluate(inputs)


def skipped(decisions: list[Decision]) -> list[Decision]:
    return [d for d in decisions if d.action is DecisionAction.SKIP]
