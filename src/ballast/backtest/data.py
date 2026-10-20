"""Historical inputs for the backtest: Pendle market series, parking rate series, ETH price series.

Everything the backtest cannot observe historically is an explicit, reported assumption
(see :class:`ballast.backtest.engine.BacktestAssumptions`).
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

from ballast.config.whitelist import Whitelist
from ballast.domain.models import Chain, Opportunity, SourceKind
from ballast.domain.money import ONE, money_context
from ballast.sources.defillama import DefiLlamaYields
from ballast.sources.http import JsonHttpClient
from ballast.sources.parsing import dec, from_unix
from ballast.sources.pendle import resolve_protocol
from ballast.storage.models import PendleHistoryRow, PendleMarketMetaRow

MAX_POINT_AGE = timedelta(hours=12)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class MarketSeries:
    chain: Chain
    market: str
    name: str
    protocol_name: str
    underlying: str
    expiry: datetime
    fee_rate: Decimal | None
    times: tuple[datetime, ...]
    implied_apy: tuple[Decimal | None, ...]
    tvl_usd: tuple[Decimal | None, ...]

    @classmethod
    def from_rows(cls, meta: PendleMarketMetaRow, rows: Sequence[PendleHistoryRow]) -> MarketSeries:
        return cls(
            chain=Chain(meta.chain_id),
            market=meta.market,
            name=meta.name,
            protocol_name=meta.protocol_name,
            underlying=meta.underlying,
            expiry=_utc(meta.expiry),
            fee_rate=meta.fee_rate,
            times=tuple(_utc(r.timestamp) for r in rows),
            implied_apy=tuple(r.implied_apy for r in rows),
            tvl_usd=tuple(r.tvl_usd for r in rows),
        )

    def point_at(self, at: datetime) -> tuple[Decimal, Decimal] | None:
        """Latest (implied APY, pool TVL) at or before ``at``, if fresh enough."""
        i = bisect.bisect_right(self.times, at) - 1
        if i < 0 or at - self.times[i] > MAX_POINT_AGE:
            return None
        apy, tvl = self.implied_apy[i], self.tvl_usd[i]
        if apy is None or tvl is None:
            return None
        return apy, tvl


@dataclass(frozen=True, slots=True)
class DailySeries:
    """Daily values keyed by date; lookups use the latest value at or before a time."""

    times: tuple[datetime, ...]
    values: tuple[Decimal, ...]

    def at(self, when: datetime) -> Decimal | None:
        i = bisect.bisect_right(self.times, when) - 1
        if i < 0 or when - self.times[i] > timedelta(days=3):
            return None
        return self.values[i]


@dataclass(frozen=True, slots=True)
class ParkingSeries:
    key: str
    protocol: str
    chain: Chain
    asset_symbol: str
    asset_address: str
    tvl_usd: Decimal
    apy: DailySeries


@dataclass(slots=True)
class HistoricalData:
    markets: list[MarketSeries]
    parking: list[ParkingSeries]
    eth_usd: DailySeries
    gaps: list[str] = field(default_factory=list)

    def opportunities_at(
        self, at: datetime, whitelist: Whitelist, chains: Sequence[Chain]
    ) -> list[Opportunity]:
        out: list[Opportunity] = []
        for m in self.markets:
            if m.chain not in chains or m.expiry <= at:
                continue
            point = m.point_at(at)
            if point is None:
                continue
            apy, tvl = point
            days = Decimal((m.expiry - at).total_seconds()) / Decimal(86400)
            if apy <= -ONE:
                continue
            with localcontext(money_context()):
                # Pendle definition: PT price in accounting units = (1 + impliedApy) ** (-days / 365).
                price = ((ONE + apy).ln() * (-days / Decimal(365))).exp()
            out.append(
                Opportunity(
                    key=f"pendle:{m.chain.slug}:{m.market}",
                    kind=SourceKind.PENDLE_PT,
                    chain=m.chain,
                    venue="pendle",
                    protocol=resolve_protocol(whitelist, m.name, m.chain, m.underlying, m.protocol_name),
                    asset_symbol=m.name,
                    asset_address=m.underlying,
                    entry_price=price,
                    payoff_per_unit=ONE,
                    maturity=m.expiry,
                    observed_at=at,
                    liquidity_usd=tvl,
                    quoted_apy=apy,
                    market_address=m.market,
                    extra={"fee_rate": str(m.fee_rate)} if m.fee_rate is not None else {},
                )
            )
        return out

    def parking_at(self, at: datetime, chains: Sequence[Chain]) -> list[Opportunity]:
        out: list[Opportunity] = []
        for p in self.parking:
            if p.chain not in chains:
                continue
            apy = p.apy.at(at)
            if apy is None:
                continue
            out.append(
                Opportunity(
                    key=p.key,
                    kind=SourceKind.PARKING,
                    chain=p.chain,
                    venue=p.protocol,
                    protocol=p.protocol,
                    asset_symbol=p.asset_symbol,
                    asset_address=p.asset_address,
                    entry_price=ONE,
                    payoff_per_unit=ONE,
                    maturity=None,
                    observed_at=at,
                    liquidity_usd=p.tvl_usd,
                    quoted_apy=apy,
                )
            )
        return out


async def load_parking_series(yields: DefiLlamaYields, chains: Sequence[Chain]) -> list[ParkingSeries]:
    pools = await yields.parking_pools(chains)
    out: list[ParkingSeries] = []
    for pool in pools:
        points = await yields.pool_history(pool.pool_id)
        points.sort()
        out.append(
            ParkingSeries(
                key=f"parking:{pool.protocol}:{pool.chain.slug}:{pool.asset_symbol}:{pool.pool_id[:8]}",
                protocol=pool.protocol,
                chain=pool.chain,
                asset_symbol=pool.asset_symbol,
                asset_address=pool.asset_address,
                tvl_usd=pool.tvl_usd,
                apy=DailySeries(tuple(t for t, _ in points), tuple(v for _, v in points)),
            )
        )
    return out


CHART_MAX_POINTS = 400  # DefiLlama coins /chart allows at most 500 points per request


async def load_eth_usd(coins: JsonHttpClient, start: datetime, days: int) -> DailySeries:
    points: dict[datetime, Decimal] = {}
    cursor = start
    remaining = days
    while remaining > 0:
        span = min(remaining, CHART_MAX_POINTS)
        body = await coins.get(
            "/chart/coingecko:ethereum", {"start": int(cursor.timestamp()), "span": span, "period": "1d"}
        )
        coin = body.get("coins", {}).get("coingecko:ethereum", {}) if isinstance(body, dict) else {}
        for row in coin.get("prices", []):
            ts, price = from_unix(row.get("timestamp")), dec(row.get("price"))
            if ts is not None and price is not None:
                points[ts] = price
        cursor += timedelta(days=span)
        remaining -= span
    ordered = sorted(points.items())
    return DailySeries(tuple(t for t, _ in ordered), tuple(v for _, v in ordered))


def coverage_gaps(markets: Sequence[MarketSeries], whitelist: Whitelist) -> list[str]:
    gaps = []
    empty = [m for m in markets if not m.times]
    if empty:
        gaps.append(f"{len(empty)} stable markets have no history points")
    wl = [m for m in markets if whitelist.find_asset(m.name, m.chain, m.underlying) is not None]
    gaps.append(f"{len(wl)} of {len(markets)} stable markets are whitelisted (tradable in the backtest)")
    return gaps
