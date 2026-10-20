"""Risk scorer: aggregates rule findings and builds allocation candidates."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from ballast.config.settings import AllocationSettings, RiskSettings
from ballast.config.whitelist import Whitelist
from ballast.domain.interfaces import IRiskRule
from ballast.domain.models import Candidate, NlyResult, Opportunity, RiskScore, Tier
from ballast.domain.money import HUNDRED, ZERO, pct
from ballast.risk.incidents import IncidentCatalog
from ballast.risk.rules import (
    AddressVerifiedRule,
    CounterpartyRule,
    DepegHistoryRule,
    LiquidityRule,
    MarketState,
    MaturityRule,
    PegRule,
    ProtocolMaturityRule,
    TvlRule,
    WhitelistRule,
    YieldAnomalyRule,
)


def exposures_for(opportunity: Opportunity, whitelist: Whitelist) -> frozenset[str]:
    """Exposure keys used by allocator limits: venue, protocols, asset, tier, chain."""
    asset = whitelist.find_asset(opportunity.asset_symbol, opportunity.chain, opportunity.asset_address)
    protocols = {opportunity.protocol, *(asset.protocols if asset else ())}
    keys = {
        f"protocol:{p}"
        for p in protocols
        if p not in whitelist.protocols or not whitelist.protocols[p].cap_exempt
    }
    if opportunity.venue not in protocols:
        keys.add(f"venue:{opportunity.venue}")
    keys.add(f"asset:{asset.symbol if asset else opportunity.asset_symbol}")
    keys.add(f"tier:{asset.tier if asset else Tier.C}")
    keys.add(f"chain:{opportunity.chain.slug}")
    return frozenset(keys)


@dataclass(frozen=True, slots=True)
class RiskScorer:
    rules: Sequence[IRiskRule]
    whitelist: Whitelist
    settings: RiskSettings
    max_score: Decimal = Decimal(60)
    # Extra entry premium per risk point: score 50 adds 1 pp APY on top of the tier premium.
    premium_pp_per_point: Decimal = Decimal("0.02")

    def score(self, opportunity: Opportunity) -> RiskScore:
        findings = tuple(rule.evaluate(opportunity) for rule in self.rules)
        asset = self.whitelist.find_asset(
            opportunity.asset_symbol, opportunity.chain, opportunity.asset_address
        )
        tier = asset.tier if asset else Tier.C
        total = min(sum((f.score for f in findings if not f.blocking), ZERO), HUNDRED)
        blocked = any(f.blocking for f in findings)
        allowed = not blocked and tier is not Tier.C and total <= self.max_score
        return RiskScore(tier=tier, score=total, findings=findings, allowed=allowed)

    def threshold_apy(self, risk: RiskScore, parking_apy: Decimal) -> Decimal:
        """Entry threshold = parking + tier premium + score premium. Tier C has no threshold."""
        if risk.tier is Tier.C:
            return Decimal("Infinity")
        return (
            parking_apy
            + self.settings.tier_premium_frac(risk.tier.value)
            + pct(risk.score * self.premium_pp_per_point)
        )

    def candidate(self, opportunity: Opportunity, nly: NlyResult, parking_apy: Decimal) -> Candidate:
        risk = self.score(opportunity)
        return Candidate(
            opportunity=opportunity,
            nly=nly,
            risk=risk,
            threshold_apy=self.threshold_apy(risk, parking_apy),
            exposures=exposures_for(opportunity, self.whitelist),
        )


def default_rules(
    *,
    whitelist: Whitelist,
    catalog: IncidentCatalog,
    state: MarketState,
    risk: RiskSettings,
    allocation: AllocationSettings,
    execution_mode: bool = False,
) -> list[IRiskRule]:
    return [
        WhitelistRule(whitelist),
        AddressVerifiedRule(whitelist, execution_mode=execution_mode),
        MaturityRule(allocation.min_maturity_days, allocation.max_maturity_days),
        ProtocolMaturityRule(whitelist, state.as_of),
        CounterpartyRule(whitelist),
        TvlRule(state, risk.min_tvl_usd, risk.tvl_drop_warn_pct, risk.tvl_drop_critical_pct),
        LiquidityRule(risk.min_market_liquidity_usd),
        PegRule(state, whitelist, risk.depeg_warn_pct, risk.depeg_critical_pct),
        DepegHistoryRule(catalog, whitelist),
        YieldAnomalyRule(state),
    ]
