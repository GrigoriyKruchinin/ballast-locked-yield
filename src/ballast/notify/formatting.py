"""Message templates for Telegram (HTML). User-facing text is Russian by project policy.

All dynamic values are HTML-escaped. Numbers are shown as estimates, never as promises.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from html import escape

from ballast.domain.models import Candidate, MonitorEvent, Severity
from ballast.pipeline.run import BookSummary

SEP = "━━━━━━━━━━━━━━━━"
BOOK_TITLES = {"all": "Все сети (Ethereum + L2)", "l2": "Только L2 (Arbitrum + Base)"}
SEVERITY_ICON = {Severity.INFO: "ℹ️", Severity.WARNING: "⚠️", Severity.CRITICAL: "🚨"}
DISCLAIMER = (
    "<i>Расчётные значения: доходность «зафиксирована» только при удержании до погашения "
    "и сохранении привязки актива.</i>"
)


def _pct(value: Decimal | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.{digits}f}%"


def _usd(value: Decimal) -> str:
    return f"${value:,.2f}".replace(",", " ")


@dataclass(frozen=True, slots=True)
class OpportunityView:
    """What an opportunity alert needs; built from a Candidate or from stored rows."""

    asset_symbol: str
    chain: str
    tier: str
    nly_apy: Decimal
    nly_monthly: Decimal
    threshold_apy: Decimal | None
    days: Decimal
    risk_score: Decimal
    gas_usd: Decimal
    fees_usd: Decimal
    slippage_usd: Decimal

    @classmethod
    def from_candidate(cls, c: Candidate) -> OpportunityView:
        return cls(
            asset_symbol=c.opportunity.asset_symbol,
            chain=c.opportunity.chain.slug,
            tier=c.risk.tier.value,
            nly_apy=c.nly.nly_apy,
            nly_monthly=c.nly.nly_monthly,
            threshold_apy=c.threshold_apy,
            days=c.nly.days,
            risk_score=c.risk.score,
            gas_usd=c.nly.costs.gas_usd,
            fees_usd=c.nly.costs.fees_usd,
            slippage_usd=c.nly.costs.slippage_usd,
        )


def format_opportunities(views: Sequence[OpportunityView], parking_apy: Decimal | None) -> str:
    lines = [
        "🟢 <b>Новые возможности выше порога</b>",
        SEP,
        f"«Парковка» сейчас: <b>{_pct(parking_apy)}</b> годовых",
        "",
    ]
    for v in views:
        lines += [
            f"<b>{escape(v.asset_symbol)}</b> · {escape(v.chain)} · уровень {escape(v.tier)}",
            f"  Чистая доходность (NLY): <b>{_pct(v.nly_apy)}</b> годовых ({_pct(v.nly_monthly)} в мес.)",
            f"  Порог входа: {_pct(v.threshold_apy)} · срок {v.days:.0f} дн. · риск {v.risk_score:.0f}/100",
            f"  Издержки: газ {_usd(v.gas_usd)}, комиссии {_usd(v.fees_usd)}, "
            f"проскальзывание {_usd(v.slippage_usd)}",
            "",
        ]
    lines += [
        "<i>NLY — сколько останется после газа, комиссий и проскальзывания, если держать до погашения.</i>",
        DISCLAIMER,
    ]
    return "\n".join(lines)


def format_book(summary: BookSummary) -> list[str]:
    title = BOOK_TITLES.get(summary.book, summary.book)
    return [
        f"📒 <b>{escape(title)}</b>",
        f"  Стоимость: <b>{_usd(summary.total_value_usd)}</b>",
        f"  В позициях: {_usd(summary.locked_usd)} · в «парковке»: {_usd(summary.parked_usd)} · "
        f"свободно: {_usd(summary.idle_usd)}",
        f"  Ставка «парковки»: {_pct(summary.parking_apy)}",
    ]


def format_daily(
    books: Sequence[tuple[BookSummary, Decimal, Decimal]],
    upcoming: Sequence[tuple[str, datetime]],
    now: datetime,
    title: str = "📊 <b>Итоги дня</b>",
) -> str:
    """``books``: (summary, return since start as fraction, return over the period as fraction)."""
    lines = [f"{title} · {now:%d.%m.%Y}", SEP]
    for summary, since_start, period in books:
        lines += format_book(summary)
        lines.append(f"  За период: <b>{_pct(period, 3)}</b> · с начала: {_pct(since_start, 3)}")
        lines.append("")
    if upcoming:
        lines.append("⏳ <b>Ближайшие погашения</b>")
        lines += [f"  {escape(name)} — {when:%d.%m.%Y}" for name, when in upcoming[:5]]
        lines.append("")
    lines.append("<i>Виртуальный портфель на живых данных, без реальных денег.</i>")
    return "\n".join(lines)


def format_events(events: Sequence[MonitorEvent]) -> str:
    lines = ["🛡 <b>Риск-события</b>", SEP]
    for e in events:
        icon = SEVERITY_ICON[e.severity]
        lines.append(f"{icon} <b>{escape(e.subject)}</b>: {escape(e.message)}")
        lines.append(f"   <i>{escape(_EXPLAIN.get(e.kind, ''))}</i>")
    return "\n".join(lines)


_EXPLAIN = {
    "depeg": "Стейблкоин отклонился от $1. Это главный риск «зафиксированной» доходности.",
    "peg_missing": "Нет свежей цены: оценка риска ухудшена до выяснения.",
    "tvl_drop": "Из протокола быстро выводят деньги: признак недоверия рынка.",
    "rate_spike": "Резкий скачок фиксированной ставки обычно значит, что рынок видит новый риск.",
    "parking_rate_move": "Ставка «парковки» заметно изменилась.",
    "source_error": "Источник данных не ответил: решения по нему отложены.",
}
