"""Pendle source: market snapshots, history and quotes from the Pendle public API.

Endpoints and units are documented in ``docs/research/data-sources.md`` (APYs are fractions,
``ptPrice`` in history is USD, asset ids are ``"{chainId}-{address}"``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal

from ballast.config.whitelist import Whitelist
from ballast.domain.models import Chain, Opportunity, SourceKind, utcnow
from ballast.logging import get_logger
from ballast.sources.http import JsonHttpClient, SourceUnavailableError
from ballast.sources.parsing import dec, dec_or_zero, parse_iso, slugify, split_asset_id

log = get_logger(__name__)

PENDLE_ROUTER_V4 = "0x888888888889758F76e7103c6CbF23ABbF58F946"
PAGE_LIMIT = 100
PRICE_BATCH = 20
HISTORY_FIELDS = (
    "timestamp,impliedApy,ptPrice,syPrice,tvl,totalTvl,totalPt,totalSy,underlyingApy,tradingVolume"
)


@dataclass(frozen=True, slots=True)
class PendleMarketSnapshot:
    chain_id: int
    market: str
    name: str
    protocol_name: str
    expiry: datetime
    is_active: bool
    pt: str
    yt: str
    sy: str
    underlying: str
    accounting: str
    implied_apy: Decimal | None
    underlying_apy: Decimal | None
    liquidity_usd: Decimal
    total_tvl_usd: Decimal
    total_pt: Decimal
    total_sy: Decimal
    fee_rate: Decimal | None
    categories: tuple[str, ...]
    observed_at: datetime
    pt_usd: Decimal | None = None
    accounting_usd: Decimal | None = None
    underlying_usd: Decimal | None = None

    @property
    def is_stable(self) -> bool:
        return "stables" in self.categories

    @property
    def pt_price_in_accounting(self) -> Decimal | None:
        if self.pt_usd is None or not self.accounting_usd:
            return None
        return self.pt_usd / self.accounting_usd


@dataclass(frozen=True, slots=True)
class PendleHistoryPoint:
    timestamp: datetime
    implied_apy: Decimal | None
    pt_usd: Decimal | None
    sy_usd: Decimal | None
    tvl_usd: Decimal | None
    total_pt: Decimal | None
    total_sy: Decimal | None
    underlying_apy: Decimal | None
    volume_usd: Decimal | None


@dataclass(frozen=True, slots=True)
class PendleQuote:
    price_impact: Decimal
    effective_apy: Decimal | None
    fee_usd: Decimal
    gas_used: int | None
    amount_out_raw: int
    tx_to: str
    tx_data: str
    required_approvals: tuple[tuple[str, int], ...]


def _market_from_json(raw: Mapping[str, object], observed_at: datetime) -> PendleMarketSnapshot | None:
    details = raw.get("details")
    details = details if isinstance(details, dict) else {}
    expiry = parse_iso(raw.get("expiry"))
    ids = {k: split_asset_id(raw.get(k)) for k in ("pt", "yt", "sy", "underlyingAsset", "accountingAsset")}
    chain_id = raw.get("chainId")
    market = raw.get("address")
    if expiry is None or not isinstance(chain_id, int) or not isinstance(market, str) or None in ids.values():
        return None
    addr = {k: v[1] for k, v in ids.items() if v is not None}
    categories = raw.get("categoryIds")
    return PendleMarketSnapshot(
        chain_id=chain_id,
        market=market,
        name=str(raw.get("name", "")),
        protocol_name=str(raw.get("protocol", "")),
        expiry=expiry,
        is_active=expiry > observed_at,
        pt=addr["pt"],
        yt=addr["yt"],
        sy=addr["sy"],
        underlying=addr["underlyingAsset"],
        accounting=addr["accountingAsset"],
        implied_apy=dec(details.get("impliedApy")),
        underlying_apy=dec(details.get("underlyingApy")),
        liquidity_usd=dec_or_zero(details.get("liquidity")),
        total_tvl_usd=dec_or_zero(details.get("totalTvl")),
        total_pt=dec_or_zero(details.get("totalPt")),
        total_sy=dec_or_zero(details.get("totalSy")),
        fee_rate=dec(details.get("feeRate")),
        categories=tuple(str(c) for c in categories) if isinstance(categories, list) else (),
        observed_at=observed_at,
    )


@dataclass(slots=True)
class PendleClient:
    http: JsonHttpClient

    async def markets(self, chain_id: int, *, active: bool) -> list[PendleMarketSnapshot]:
        now = utcnow()
        out: list[PendleMarketSnapshot] = []
        skip = 0
        while True:
            params: dict[str, str | int] = {
                "chainId": chain_id,
                "isActive": str(active).lower(),
                "limit": PAGE_LIMIT,
                "skip": skip,
            }
            page = await self.http.get("/v2/markets/all", params)
            results = page.get("results", []) if isinstance(page, dict) else []
            for raw in results:
                snap = _market_from_json(raw, now) if isinstance(raw, dict) else None
                if snap is not None:
                    out.append(snap)
            total = page.get("total", 0) if isinstance(page, dict) else 0
            skip += PAGE_LIMIT
            if not results or skip >= int(total):
                return out

    async def prices(self, chain_id: int, addresses: Iterable[str]) -> dict[str, Decimal]:
        """USD prices keyed by lowercase address."""
        unique = sorted({a.lower() for a in addresses})
        out: dict[str, Decimal] = {}
        for i in range(0, len(unique), PRICE_BATCH):
            batch = unique[i : i + PRICE_BATCH]
            ids = ",".join(f"{chain_id}-{a}" for a in batch)
            body = await self.http.get("/v1/prices/assets", {"chainId": chain_id, "ids": ids})
            prices = body.get("prices", {}) if isinstance(body, dict) else {}
            for asset_id, price in prices.items():
                parsed = split_asset_id(asset_id)
                value = dec(price)
                if parsed is not None and value is not None:
                    out[parsed[1].lower()] = value
        return out

    async def history(
        self, chain_id: int, market: str, start: datetime, end: datetime, time_frame: str = "hour"
    ) -> list[PendleHistoryPoint]:
        body = await self.http.get(
            f"/v3/{chain_id}/markets/{market}/historical-data",
            {
                "time_frame": time_frame,
                "timestamp_start": start.isoformat(),
                "timestamp_end": end.isoformat(),
                "fields": HISTORY_FIELDS,
            },
        )
        results = body.get("results", []) if isinstance(body, dict) else []
        points: list[PendleHistoryPoint] = []
        for row in results:
            if not isinstance(row, dict):
                continue
            ts = parse_iso(row.get("timestamp"))
            if ts is None:
                continue
            points.append(
                PendleHistoryPoint(
                    timestamp=ts,
                    implied_apy=dec(row.get("impliedApy")),
                    pt_usd=dec(row.get("ptPrice")),
                    sy_usd=dec(row.get("syPrice")),
                    tvl_usd=dec(row.get("tvl")),
                    total_pt=dec(row.get("totalPt")),
                    total_sy=dec(row.get("totalSy")),
                    underlying_apy=dec(row.get("underlyingApy")),
                    volume_usd=dec(row.get("tradingVolume")),
                )
            )
        return points

    async def quote(
        self,
        chain_id: int,
        token_in: str,
        amount_in_raw: int,
        token_out: str,
        *,
        receiver: str,
        slippage: Decimal,
        use_limit_order: bool,
    ) -> PendleQuote | None:
        body = await self.http.post(
            f"/v3/sdk/{chain_id}/convert",
            {
                "receiver": receiver,
                "slippage": float(slippage),
                "inputs": [{"token": token_in, "amount": str(amount_in_raw)}],
                "outputs": [token_out],
                "useLimitOrder": use_limit_order,
                "enableAggregator": False,
                "additionalData": "impliedApy,effectiveApy",
            },
        )
        routes = body.get("routes", []) if isinstance(body, dict) else []
        if not routes or not isinstance(routes[0], dict):
            return None
        route = routes[0]
        data = route.get("data", {})
        tx = route.get("tx", {})
        outputs = route.get("outputs", [])
        if not isinstance(data, dict) or not isinstance(tx, dict) or not outputs:
            return None
        approvals = body.get("requiredApprovals", []) if isinstance(body, dict) else []
        fee = data.get("fee", {})
        gas = data.get("gasUsed")
        return PendleQuote(
            price_impact=dec_or_zero(data.get("priceImpact")),
            effective_apy=dec(data.get("effectiveApy")),
            fee_usd=dec_or_zero(fee.get("usd") if isinstance(fee, dict) else None),
            gas_used=int(gas) if isinstance(gas, str | int) and str(gas).isdigit() else None,
            amount_out_raw=int(outputs[0].get("amount", 0)),
            tx_to=str(tx.get("to", "")),
            tx_data=str(tx.get("data", "")),
            required_approvals=tuple(
                (str(a.get("token", "")), int(a.get("amount", 0))) for a in approvals if isinstance(a, dict)
            ),
        )


async def snapshot_chain(client: PendleClient, chain_id: int) -> list[PendleMarketSnapshot]:
    """Active markets of one chain with PT / accounting / underlying USD prices attached."""
    markets = await client.markets(chain_id, active=True)
    if not markets:
        return []
    addresses = [a for m in markets for a in (m.pt, m.accounting, m.underlying)]
    prices = await client.prices(chain_id, addresses)
    return [
        replace(
            m,
            pt_usd=prices.get(m.pt.lower()),
            accounting_usd=prices.get(m.accounting.lower()),
            underlying_usd=prices.get(m.underlying.lower()),
        )
        for m in markets
    ]


def resolve_protocol(
    whitelist: Whitelist, name: str, chain: Chain, underlying: str, protocol_name: str
) -> str:
    """Yield-bearing protocol of a market: the whitelisted asset's first capped protocol, else a slug."""
    asset = whitelist.find_asset(name, chain, underlying)
    if asset is not None:
        capped = [p for p in asset.protocols if not whitelist.protocols[p].cap_exempt]
        return (capped or list(asset.protocols))[0]
    return slugify(protocol_name) or "unknown"


@dataclass(frozen=True, slots=True)
class PendleNormalizer:
    """Turns snapshots on portfolio chains into :class:`Opportunity` objects."""

    whitelist: Whitelist

    def normalize(self, snap: PendleMarketSnapshot) -> Opportunity | None:
        try:
            chain = Chain(snap.chain_id)
        except ValueError:
            return None  # observe-only chain
        price = snap.pt_price_in_accounting
        if price is None or snap.accounting_usd is None or not snap.is_active:
            return None
        protocol = resolve_protocol(self.whitelist, snap.name, chain, snap.underlying, snap.protocol_name)
        return Opportunity(
            key=f"pendle:{chain.slug}:{snap.market.lower()}",
            kind=SourceKind.PENDLE_PT,
            chain=chain,
            venue="pendle",
            protocol=protocol,
            asset_symbol=snap.name,
            asset_address=snap.underlying,
            entry_price=price,
            payoff_per_unit=Decimal(1),
            maturity=snap.expiry,
            observed_at=snap.observed_at,
            liquidity_usd=snap.liquidity_usd,
            asset_usd_price=snap.accounting_usd,
            quoted_apy=snap.implied_apy,
            market_address=snap.market,
            extra={
                "pt": snap.pt,
                "sy": snap.sy,
                "accounting": snap.accounting,
                "protocol_name": snap.protocol_name,
                **({"fee_rate": str(snap.fee_rate)} if snap.fee_rate is not None else {}),
            },
        )


@dataclass(slots=True)
class PendleSource:
    """Implements :class:`ballast.domain.interfaces.ILockedYieldSource` for portfolio chains."""

    client: PendleClient
    normalizer: PendleNormalizer
    chains: Sequence[Chain]
    last_snapshots: list[PendleMarketSnapshot] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "pendle"

    async def fetch(self) -> Sequence[Opportunity]:
        snaps: list[PendleMarketSnapshot] = []
        for chain in self.chains:
            try:
                snaps.extend(await snapshot_chain(self.client, int(chain)))
            except SourceUnavailableError as exc:
                log.warning("pendle_chain_failed", chain=chain.slug, error=str(exc))
        self.last_snapshots = snaps
        opportunities = [self.normalizer.normalize(s) for s in snaps]
        return [o for o in opportunities if o is not None]
