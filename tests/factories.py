"""Test factories for domain objects."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ballast.domain.models import Chain, CostEstimate, Opportunity, SourceKind

NOW = datetime(2026, 1, 1, tzinfo=UTC)
ZERO_COSTS = CostEstimate(Decimal(0), Decimal(0), Decimal(0), Decimal(0), Decimal(0))


def pt(
    *,
    key: str = "pendle:arbitrum:0xmarket",
    price: str = "0.98",
    days: int = 90,
    asset: str = "USDC",
    protocol: str = "aave-v3",
    asset_usd: str = "1",
    liquidity: str = "5000000",
    chain: Chain = Chain.ARBITRUM,
    now: datetime = NOW,
) -> Opportunity:
    return Opportunity(
        key=key,
        kind=SourceKind.PENDLE_PT,
        chain=chain,
        venue="pendle",
        protocol=protocol,
        asset_symbol=asset,
        asset_address=None,
        entry_price=Decimal(price),
        payoff_per_unit=Decimal(1),
        maturity=now + timedelta(days=days),
        observed_at=now,
        liquidity_usd=Decimal(liquidity),
        asset_usd_price=Decimal(asset_usd),
    )


def parking(
    *, key: str = "aave-v3:arbitrum:USDC", apy: str = "0.05", protocol: str = "aave-v3"
) -> Opportunity:
    return Opportunity(
        key=key,
        kind=SourceKind.PARKING,
        chain=Chain.ARBITRUM,
        venue=protocol,
        protocol=protocol,
        asset_symbol="USDC",
        asset_address=None,
        entry_price=Decimal(1),
        payoff_per_unit=Decimal(1),
        maturity=None,
        observed_at=NOW,
        liquidity_usd=Decimal(100_000_000),
        quoted_apy=Decimal(apy),
    )


class FixedCostModel:
    def __init__(self, costs: CostEstimate = ZERO_COSTS) -> None:
        self.costs = costs

    def estimate(self, opportunity: Opportunity, capital_usd: Decimal) -> CostEstimate:
        return self.costs
