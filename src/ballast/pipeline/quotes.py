"""Hourly Pendle convert quotes (price impact) for whitelisted, risk-allowed candidates.

Both AMM-only (``useLimitOrder=false``, used by the cost model: conservative) and with limit
orders (recorded for comparison). Quotes are taken for the underlying token -> PT at fixed sizes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select

from ballast.chains.client import RpcError, decode_result, encode_call
from ballast.domain.models import Chain, utcnow
from ballast.domain.money import TokenAmount
from ballast.logging import get_logger
from ballast.pipeline.context import AppContext
from ballast.sources.http import SourceUnavailableError
from ballast.sources.pendle import PENDLE_ROUTER_V4
from ballast.storage.db import session_scope
from ballast.storage.models import PendleMarketSnapshotRow, RiskScoreRow, ScanRow
from ballast.storage.repositories import SnapshotRepository

log = get_logger(__name__)

QUOTE_SIZES_USD = (Decimal(1000), Decimal(5000))
QUOTE_RECEIVER = "0x000000000000000000000000000000000000dEaD"
QUOTE_SLIPPAGE = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class QuoteTarget:
    key: str
    chain: Chain
    underlying: str
    underlying_usd: Decimal
    pt: str


async def _targets(ctx: AppContext) -> list[QuoteTarget]:
    async with session_scope(ctx.sessions) as session:
        last_run = await session.scalar(select(func.max(ScanRow.started_at)))
        if last_run is None:
            return []
        scan_ids = select(ScanRow.id).where(ScanRow.started_at == last_run)
        keys = set(
            (
                await session.scalars(
                    select(RiskScoreRow.opportunity_key).where(
                        RiskScoreRow.scan_id.in_(scan_ids),
                        RiskScoreRow.allowed.is_(True),
                        RiskScoreRow.opportunity_key.like("pendle:%"),
                    )
                )
            ).all()
        )
        targets: list[QuoteTarget] = []
        for key in sorted(keys):
            _, slug, market = key.split(":", 2)
            chain = Chain.from_slug(slug)
            snap = await session.scalar(
                select(PendleMarketSnapshotRow)
                .where(
                    PendleMarketSnapshotRow.chain_id == int(chain), PendleMarketSnapshotRow.market == market
                )
                .order_by(PendleMarketSnapshotRow.observed_at.desc())
                .limit(1)
            )
            if snap is not None and snap.underlying_usd:
                targets.append(QuoteTarget(key, chain, snap.underlying, snap.underlying_usd, snap.pt))
    return targets


async def refresh_quotes(ctx: AppContext, sizes: Sequence[Decimal] = QUOTE_SIZES_USD) -> int:
    stored = 0
    for target in await _targets(ctx):
        client = ctx.chain_clients.get(target.chain)
        if client is None:
            continue
        try:
            (decimals,) = decode_result(
                ["uint8"], await client.call(target.underlying, encode_call("decimals()", [], []))
            )
        except (RpcError, ValueError) as exc:
            log.warning("quote_decimals_failed", key=target.key, error=str(exc))
            continue
        for size in sizes:
            amount = TokenAmount(size / target.underlying_usd, int(decimals)).to_raw(receive=False)
            for use_limit_order in (False, True):
                try:
                    quote = await ctx.pendle.quote(
                        int(target.chain),
                        target.underlying,
                        amount,
                        target.pt,
                        receiver=QUOTE_RECEIVER,
                        slippage=QUOTE_SLIPPAGE,
                        use_limit_order=use_limit_order,
                    )
                except SourceUnavailableError as exc:
                    log.warning("quote_failed", key=target.key, error=str(exc))
                    continue
                if quote is None:
                    continue
                if quote.tx_to.lower() != PENDLE_ROUTER_V4.lower():
                    log.warning("quote_unexpected_router", key=target.key, to=quote.tx_to)
                    continue
                async with session_scope(ctx.sessions) as session:
                    SnapshotRepository(session).add_quote(
                        target.key,
                        size,
                        use_limit_order=use_limit_order,
                        price_impact=quote.price_impact,
                        effective_apy=quote.effective_apy,
                        fee_usd=quote.fee_usd,
                        gas_used=quote.gas_used,
                        observed_at=utcnow(),
                    )
                stored += 1
    return stored
