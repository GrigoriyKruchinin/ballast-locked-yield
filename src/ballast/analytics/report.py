"""Stage 1 Go/No-Go report generator (data sections).

Produces ``docs/reports/stage-1-go-no-go.md``. The final recommendation and the plain-language
("Простыми словами") block are written by a human/agent reviewing these numbers, not generated.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from statistics import median

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ballast.domain.models import Chain, Tier
from ballast.domain.money import DAYS_PER_MONTH, DAYS_PER_YEAR, ZERO
from ballast.risk.incidents import IncidentCatalog
from ballast.storage.models import (
    NlyResultRow,
    PaperDailyRow,
    PaperPositionRow,
    PendleMarketSnapshotRow,
    RiskScoreRow,
    ScanRow,
    SimulatedTxRow,
)

MIN_LIVE_DAYS = 28


def _pct(v: Decimal | None, digits: int = 2) -> str:
    return "n/a" if v is None else f"{v * 100:.{digits}f}%"


@dataclass(frozen=True, slots=True)
class LiveBook:
    book: str
    first_day: date
    last_day: date
    days: int
    start_value: Decimal
    end_value: Decimal
    net_monthly: Decimal
    net_apy: Decimal
    parking_avg: Decimal
    weeks_total: int
    weeks_locked_70: int
    costs_paid: Decimal
    cost_share: Decimal | None
    open_positions: int


async def live_books(session: AsyncSession, books: list[str]) -> list[LiveBook]:
    out: list[LiveBook] = []
    for book in books:
        rows = list(
            (
                await session.scalars(
                    select(PaperDailyRow).where(PaperDailyRow.portfolio == book).order_by(PaperDailyRow.day)
                )
            ).all()
        )
        if not rows:
            continue
        first, last = rows[0], rows[-1]
        days = max((last.day - first.day).days, 1)
        start_value = (
            Decimal(10_000) if first.total_value_usd <= 0 else first.total_value_usd + first.costs_paid_usd
        )
        ret = last.total_value_usd / start_value - 1
        parking = [r.parking_apy for r in rows if r.parking_apy is not None]
        weeks: defaultdict[tuple[int, int], list[Decimal]] = defaultdict(list)
        for r in rows:
            iso = r.day.isocalendar()
            weeks[(iso.year, iso.week)].append(
                r.locked_usd / r.total_value_usd if r.total_value_usd else ZERO
            )
        gross = last.total_value_usd - start_value + last.costs_paid_usd
        open_n = await session.scalar(
            select(func.count(PaperPositionRow.id)).where(
                PaperPositionRow.portfolio == book,
                PaperPositionRow.status == "open",
                PaperPositionRow.kind != "parking",
            )
        )
        out.append(
            LiveBook(
                book=book,
                first_day=first.day,
                last_day=last.day,
                days=days,
                start_value=start_value,
                end_value=last.total_value_usd,
                net_monthly=ret * DAYS_PER_MONTH / days,
                net_apy=ret * DAYS_PER_YEAR / days,
                parking_avg=sum(parking, ZERO) / len(parking) if parking else ZERO,
                weeks_total=len(weeks),
                weeks_locked_70=sum(1 for v in weeks.values() if sum(v, ZERO) / len(v) >= Decimal("0.7")),
                costs_paid=last.costs_paid_usd,
                cost_share=last.costs_paid_usd / gross if gross > 0 else None,
                open_positions=int(open_n or 0),
            )
        )
    return out


async def nly_distribution(session: AsyncSession) -> dict[str, list[Decimal]]:
    """NLY APY of risk-allowed vs blocked Pendle candidates, one value per (scan, market)."""
    rows = await session.execute(
        select(NlyResultRow.nly_apy, RiskScoreRow.allowed, RiskScoreRow.tier)
        .join(
            RiskScoreRow,
            (RiskScoreRow.scan_id == NlyResultRow.scan_id)
            & (RiskScoreRow.opportunity_key == NlyResultRow.opportunity_key),
        )
        .where(NlyResultRow.kind == "pendle_pt")
    )
    out: dict[str, list[Decimal]] = defaultdict(list)
    for nly, allowed, tier in rows.all():
        out[f"tier {tier} {'allowed' if allowed else 'blocked'}"].append(nly)
    return dict(out)


async def weekly_opportunities(session: AsyncSession) -> list[tuple[str, int, int]]:
    """Per ISO week: scans run, scans with >= 1 eligible opportunity."""
    rows = await session.execute(select(ScanRow.started_at, ScanRow.n_eligible, ScanRow.notes))
    weeks: defaultdict[str, list[int]] = defaultdict(lambda: [0, 0])
    for started, eligible, notes in rows.all():
        if "book=all" not in (notes or ""):
            continue
        iso = started.isocalendar()
        key = f"{iso.year}-W{iso.week:02d}"
        weeks[key][0] += 1
        weeks[key][1] += 1 if eligible else 0
    return [(k, v[0], v[1]) for k, v in sorted(weeks.items())]


async def fork_summary(session: AsyncSession) -> tuple[int, int, Decimal | None]:
    rows = (await session.scalars(select(SimulatedTxRow))).all()
    entries = [r for r in rows if r.description.startswith("entry")]
    within = sum(1 for r in entries if r.within_tolerance)
    devs = [abs(r.deviation_pct) for r in entries if r.deviation_pct is not None]
    return len(entries), within, max(devs) if devs else None


async def universe(session: AsyncSession) -> list[tuple[int, int, int, int]]:
    """Per chain id: distinct markets observed, stable markets, whitelisted markets."""
    rows = await session.execute(
        select(
            PendleMarketSnapshotRow.chain_id,
            func.count(func.distinct(PendleMarketSnapshotRow.market)),
            func.count(func.distinct(PendleMarketSnapshotRow.market)).filter(
                PendleMarketSnapshotRow.is_stable
            ),
            func.count(func.distinct(PendleMarketSnapshotRow.market)).filter(
                PendleMarketSnapshotRow.is_whitelisted
            ),
        ).group_by(PendleMarketSnapshotRow.chain_id)
    )
    return [(int(c), int(a), int(b), int(w)) for c, a, b, w in rows.all()]


async def tier_premiums(session: AsyncSession) -> dict[str, Decimal]:
    """Average quoted implied APY minus the parking benchmark, per tier, over all live scans."""
    rows = await session.execute(
        select(RiskScoreRow.tier, NlyResultRow.quoted_apy, ScanRow.parking_apy)
        .join(
            NlyResultRow,
            (NlyResultRow.scan_id == RiskScoreRow.scan_id)
            & (NlyResultRow.opportunity_key == RiskScoreRow.opportunity_key),
        )
        .join(ScanRow, ScanRow.id == RiskScoreRow.scan_id)
        .where(NlyResultRow.kind == "pendle_pt", RiskScoreRow.tier.in_(["A", "B"]))
    )
    acc: defaultdict[str, list[Decimal]] = defaultdict(list)
    for tier, quoted, parking in rows.all():
        if quoted is not None and parking is not None:
            acc[tier].append(quoted - parking)
    return {t: sum(v, ZERO) / len(v) for t, v in acc.items() if v}


def _quantiles(values: list[Decimal]) -> str:
    if not values:
        return "n/a"
    s = sorted(values)

    def q(p: float) -> Decimal:
        return s[min(int(p * (len(s) - 1)), len(s) - 1)]

    return f"p10 {_pct(q(0.1))} · median {_pct(Decimal(str(median(s))))} · p90 {_pct(q(0.9))} · n={len(s)}"


async def render_go_no_go(session: AsyncSession, catalog: IncidentCatalog, books: list[str]) -> str:
    now = datetime.now(UTC)
    live = await live_books(session, books)
    dist = await nly_distribution(session)
    weeks = await weekly_opportunities(session)
    fork_n, fork_ok, fork_max = await fork_summary(session)
    uni = await universe(session)
    premiums = await tier_premiums(session)
    first, last = date(2022, 1, 1), now.date()
    loss_a = catalog.annual_expected_loss(Tier.A, first, last, 8)
    loss_b = catalog.annual_expected_loss(Tier.B, first, last, 30)
    chain_names = {int(c): c.slug for c in Chain}

    lines = [
        "# Stage 1 Go/No-Go report",
        "",
        f"_Generated {now:%Y-%m-%d %H:%M} UTC by `ballast report`. Results are estimated/historical, not promises._",
        "",
        "## Простыми словами",
        "",
        "_To be written after reviewing the numbers below (3–6 sentences, Russian)._",
        "",
        "## Recommendation",
        "",
        "_Go / Rework / No-Go: to be decided with the user after the live window (>= 4 weeks)._",
        "",
        "## Live paper portfolio",
        "",
    ]
    if not live:
        lines.append("No live data yet.")
    for b in live:
        enough = "" if b.days >= MIN_LIVE_DAYS else f" ⚠️ only {b.days} days (< {MIN_LIVE_DAYS})"
        lines += [
            f"### Book `{b.book}` ({b.first_day} → {b.last_day}, {b.days} days){enough}",
            "",
            f"- Value: {b.start_value:,.2f} → {b.end_value:,.2f} USD; net {_pct(b.net_monthly, 3)} / month, "
            f"{_pct(b.net_apy)} APY (simple).",
            f"- Parking benchmark (best whitelisted rate, average): {_pct(b.parking_avg)}; "
            f"excess {(b.net_apy - b.parking_avg) * 100:.2f} pp.",
            f"- Weeks with >= 70% locked: {b.weeks_locked_70}/{b.weeks_total}. Open locked positions: {b.open_positions}.",
            f"- Costs paid: {b.costs_paid:,.2f} USD ({_pct(b.cost_share, 1)} of gross yield).",
            "",
        ]
    lines += [
        "## Opportunity universe (distinct Pendle markets observed)",
        "",
        "| Chain | Markets | Stable | Whitelisted |",
        "|---|---|---|---|",
    ]
    lines += [f"| {chain_names.get(c, str(c))} | {a} | {s} | {w} |" for c, a, s, w in sorted(uni)]
    lines += ["", "## NLY distribution (per scan and market, after costs)", ""]
    lines += [f"- {k}: {_quantiles(v)}" for k, v in sorted(dist.items())] or ["- no data"]
    lines += [
        "",
        "## Opportunities by week (book `all`)",
        "",
        "| Week | Scans | Scans with >= 1 eligible |",
        "|---|---|---|",
    ]
    lines += [f"| {w} | {n} | {e} |" for w, n, e in weeks]
    lines += [
        "",
        "## Fork simulation",
        "",
        f"- Entry simulations: {fork_n}; within 0.3% of the model: {fork_ok}; max |deviation|: {_pct(fork_max, 4)}.",
        "",
        "## Risk premium vs incident losses (does the risk pay?)",
        "",
        "| Tier | Avg quoted premium over parking (live) | Expected incident loss per year (catalog) | Premium net of loss |",
        "|---|---|---|---|",
    ]
    for tier, loss in (("A", loss_a), ("B", loss_b)):
        prem = premiums.get(tier)
        net = None if prem is None else prem - loss
        lines.append(f"| {tier} | {_pct(prem)} | {_pct(loss)} | {_pct(net)} |")
    lines += [
        "",
        f"Incident catalog: {len(catalog.incidents)} sourced entries (`config/incidents.yaml`, `docs/research/incidents.md`).",
        "Base rates use comparable-asset universes of 8 (A) and 30 (B); small samples, order-of-magnitude only.",
        "",
        "## Backtest",
        "",
        "See the latest `docs/reports/backtest-*.md` (out-of-sample validation window).",
        "",
        "## Data weaknesses",
        "",
        "- Gas history and historical slippage are modelled, not observed (see backtest assumptions).",
        "- Peg and TVL screens are not available point-in-time for the backtest.",
        "- The whitelisted universe on the target chains is small; sample sizes are reported above.",
        "",
    ]
    return "\n".join(lines)


def write(text: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
