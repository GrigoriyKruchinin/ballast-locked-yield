"""Load history, run the out-of-sample backtest for each book, store and render the report."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select

from ballast.analytics.criteria import Criterion, evaluate_criteria, verdict
from ballast.backtest.data import (
    HistoricalData,
    MarketSeries,
    coverage_gaps,
    load_eth_usd,
    load_parking_series,
)
from ballast.backtest.engine import (
    BacktestAssumptions,
    Backtester,
    BacktestResult,
    OutOfSampleReport,
    out_of_sample,
)
from ballast.pipeline.context import AppContext
from ballast.sources.defillama import DefiLlamaYields
from ballast.sources.http import JsonHttpClient
from ballast.storage.db import session_scope
from ballast.storage.models import BacktestRunRow, PendleHistoryRow
from ballast.storage.repositories import HistoryRepository

LOOKBACK = timedelta(days=548)


@dataclass(frozen=True, slots=True)
class BookBacktest:
    book: str
    report: OutOfSampleReport
    criteria: list[Criterion]
    verdict: str


async def load_history(ctx: AppContext) -> HistoricalData:
    async with session_scope(ctx.sessions) as session:
        repo = HistoryRepository(session)
        metas = await repo.markets(stable_only=True)
        markets = [MarketSeries.from_rows(m, await repo.points(m.chain_id, m.market)) for m in metas]
    data_settings = ctx.settings.data
    if data_settings is None:
        msg = "data settings missing"
        raise RuntimeError(msg)
    async with JsonHttpClient(data_settings.defillama_yields_url, timeout_seconds=90) as yields_http:
        parking = await load_parking_series(
            DefiLlamaYields(yields_http, ctx.whitelist), list(ctx.chain_clients)
        )
    start = datetime.now(UTC) - LOOKBACK
    async with JsonHttpClient(data_settings.defillama_coins_url, timeout_seconds=60) as coins:
        eth = await load_eth_usd(coins, start, LOOKBACK.days + 2)
    data = HistoricalData([m for m in markets if m.times], parking, eth)
    data.gaps.extend(coverage_gaps(markets, ctx.whitelist))
    if not eth.times:
        data.gaps.append("ETH price history unavailable: fallback price used")
    return data


async def history_window(ctx: AppContext) -> tuple[datetime, datetime]:
    async with session_scope(ctx.sessions) as session:
        first = await session.scalar(select(func.min(PendleHistoryRow.timestamp)))
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    floor = now - LOOKBACK
    if first is None:
        return floor, now
    first = first if first.tzinfo else first.replace(tzinfo=UTC)
    return max(first, floor).replace(hour=0, minute=0, second=0, microsecond=0), now - timedelta(days=1)


async def run_backtests(
    ctx: AppContext,
) -> tuple[list[BookBacktest], HistoricalData, tuple[datetime, datetime]]:
    alloc, costs = ctx.settings.allocation, ctx.settings.costs
    if alloc is None or costs is None:
        msg = "allocation/cost settings missing"
        raise RuntimeError(msg)
    data = await load_history(ctx)
    start, end = await history_window(ctx)
    bt = Backtester(data, ctx.whitelist, ctx.catalog, alloc, costs, BacktestAssumptions())
    out: list[BookBacktest] = []
    for book in ctx.books:
        report = out_of_sample(bt, start, end, book.chains)
        criteria = evaluate_criteria(report.validation_default.metrics)
        out.append(BookBacktest(book.name, report, criteria, verdict(criteria)))
    async with session_scope(ctx.sessions) as session:
        for b in out:
            for window, result in (
                ("validation-default", b.report.validation_default),
                ("validation-chosen", b.report.validation_chosen),
                *(("tuning", r) for r in b.report.tuning),
            ):
                session.add(_row(b.book, window, result))
    return out, data, (start, end)


def _row(book: str, window: str, r: BacktestResult) -> BacktestRunRow:
    m = r.metrics
    return BacktestRunRow(
        created_at=datetime.now(UTC),
        window=window[:16],
        start=r.start,
        end=r.end,
        params={
            "book": book,
            "label": r.label,
            "premiums": [str(p) for p in r.premiums],
            "chains": [c.slug for c in r.chains],
        },
        metrics={k: str(getattr(m, k)) for k in m.__dataclass_fields__},
    )


def _closeness(r: BacktestResult) -> str:
    m = r.metrics
    if not m.days_with_allowed_candidate:
        return "No risk-allowed whitelisted candidate existed on any day of the validation window."
    decision_days = len(r.records)
    return (
        f"Risk-allowed candidates existed on {m.days_with_allowed_candidate} of {decision_days} decision days. "
        f"Best NLY minus entry threshold: median {m.median_best_excess_pp:.2f} pp, "
        f"best day {m.max_best_excess_pp:.2f} pp (positive would mean an entry)."
    )


def _pct(v: Decimal | None, digits: int = 2) -> str:
    return "n/a" if v is None else f"{v * 100:.{digits}f}%"


def _metrics_table(results: list[tuple[str, BacktestResult]]) -> list[str]:
    lines = [
        "| Run | Window | Premiums A/B (pp) | Net APY | Net / month | After incidents | Parking avg "
        "| Excess (pp) | Avg locked | Weeks >=70% | Cost share | Max protocol | Max tier B | Positions |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for label, r in results:
        m = r.metrics
        lines.append(
            f"| {label} | {r.start:%Y-%m-%d} → {r.end:%Y-%m-%d} | {r.premiums[0]}/{r.premiums[1]} "
            f"| {_pct(m.net_apy)} | {_pct(m.net_monthly, 3)} | {_pct(m.net_apy_after_incidents)} "
            f"| {_pct(m.parking_apy_avg)} | {m.excess_pp:.2f} | {_pct(m.avg_locked_share, 1)} "
            f"| {m.weeks_deployed_70}/{m.weeks_total} | {_pct(m.cost_share_of_gross, 1)} "
            f"| {_pct(m.max_protocol_share, 1)} | {_pct(m.max_tier_b_share, 1)} | {m.positions_opened} |"
        )
    return lines


def render_report(
    books: list[BookBacktest],
    data: HistoricalData,
    window: tuple[datetime, datetime],
    assumptions: BacktestAssumptions,
) -> str:
    start, end = window
    with_history = len(data.markets)
    lines = [
        f"# Backtest report ({datetime.now(UTC):%Y-%m-%d})",
        "",
        "> Historical simulation, not a promise. Results hold only under the assumptions below.",
        "",
        "## Data coverage",
        "",
        f"- Window: {start:%Y-%m-%d} → {end:%Y-%m-%d}; stable Pendle markets with history: {with_history}.",
        *[f"- {g}" for g in data.gaps],
        f"- Parking series: {len(data.parking)} pools (DefiLlama daily `apyBase`).",
        "",
        "## Assumptions",
        "",
        *[f"- {a}" for a in assumptions.describe()],
        "",
        "## Method",
        "",
        "- The live decision code (NLY, risk rules, allocator, paper portfolio) replays history daily.",
        "- Tier premiums are chosen on the first 60% of the window (tuning) by incident-adjusted APY;"
        " the remaining 40% (validation) is never used for tuning.",
        "- Go/No-Go criteria use the validation window with the agreed default premiums (A 2 pp, B 6 pp).",
        "",
    ]
    for b in books:
        r = b.report
        lines += [
            f"## Book `{b.book}` ({', '.join(c.slug for c in r.validation_default.chains)})",
            "",
            f"Premiums chosen on tuning window: A {r.chosen[0]} pp, B {r.chosen[1]} pp.",
            "",
            *_metrics_table(
                [("validation (default)", r.validation_default), ("validation (chosen)", r.validation_chosen)]
                + [(f"tuning {t.premiums[0]}/{t.premiums[1]}", t) for t in r.tuning]
            ),
            "",
            "### Criteria (validation, default premiums)",
            "",
            "| Criterion | Value | Threshold | Result |",
            "|---|---|---|---|",
            *[
                f"| {c.name}{' (mandatory)' if c.mandatory else ''} | {c.value} | {c.threshold} | "
                f"{'n/a' if c.passed is None else ('pass' if c.passed else 'FAIL')} |"
                for c in b.criteria
            ],
            "",
            f"Backtest-only verdict: **{b.verdict}** (final verdict needs live collection and fork checks).",
            "",
            _closeness(r.validation_default),
            "",
            "Entered markets (validation, default): "
            + (", ".join(sorted(set(r.validation_default.opened))) or "none"),
            "",
        ]
    return "\n".join(lines)


def write_report(text: str, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"backtest-{datetime.now(UTC):%Y-%m-%d}.md"
    path.write_text(text, encoding="utf-8")
    return path
