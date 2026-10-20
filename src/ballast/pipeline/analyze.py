"""Analysis step: NLY + risk for every opportunity, then an allocation plan.

Pure with respect to I/O: takes collected data and settings, returns results. Persistence is the
caller's job. The same function serves live scans, the paper portfolio and the backtest.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from ballast.allocation.allocator import GreedyAllocator
from ballast.config.settings import AllocationSettings, CostSettings, RiskSettings
from ballast.config.whitelist import Whitelist
from ballast.costs.model import GasSnapshot, LiquidityImpactSlippage, QuotedSlippage, RoundTripCostModel
from ballast.domain.models import (
    Allocation,
    AllocationPlan,
    Candidate,
    Chain,
    Decision,
    DecisionAction,
    Opportunity,
    SourceKind,
)
from ballast.domain.money import ZERO, pct
from ballast.nly.calculator import NlyCalculator, NlyError
from ballast.risk.incidents import IncidentCatalog
from ballast.risk.rules import MarketState
from ballast.risk.scorer import RiskScorer, default_rules

# Pendle swap fee is charged in rate terms (feeRate ~0.1-0.2% APR);
# 10 bps of notional covers it for terms up to 6 months.
PENDLE_VENUE_FEE_BPS = Decimal(10)


@dataclass(frozen=True, slots=True)
class AnalysisInputs:
    opportunities: Sequence[Opportunity]
    parking: Sequence[Opportunity]
    gas: Mapping[Chain, GasSnapshot]
    state: MarketState
    quotes: Mapping[str, Mapping[Decimal, Decimal]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    candidates: tuple[Candidate, ...]
    parking: tuple[Candidate, ...]
    plan: AllocationPlan
    parking_apy: Decimal
    min_positions: Mapping[Chain, Decimal]
    errors: tuple[Decision, ...]

    @property
    def eligible(self) -> list[Candidate]:
        return [c for c in self.candidates if c.risk.allowed and c.nly.nly_apy >= c.threshold_apy]


def reference_parking_apy(parking: Sequence[Opportunity]) -> Decimal:
    """Benchmark parking rate: the best whitelisted rate (conservative: raises the entry bar)."""
    rates = [p.quoted_apy for p in parking if p.quoted_apy is not None]
    return max(rates, default=ZERO)


@dataclass(frozen=True, slots=True)
class Analyzer:
    whitelist: Whitelist
    catalog: IncidentCatalog
    risk: RiskSettings
    allocation: AllocationSettings
    costs: CostSettings
    strict_addresses: bool = False

    def cost_model(self, inputs: AnalysisInputs) -> RoundTripCostModel:
        return RoundTripCostModel(
            gas=inputs.gas,
            slippage=QuotedSlippage(inputs.quotes, fallback=LiquidityImpactSlippage()),
            entry_gas_units=self.costs.entry_gas_units,
            exit_gas_units=self.costs.exit_gas_units,
            swap_fee_bps=self.costs.default_swap_fee_bps,
            venue_fee_bps={SourceKind.PENDLE_PT: PENDLE_VENUE_FEE_BPS},
        )

    def run(
        self,
        inputs: AnalysisInputs,
        *,
        capital_usd: Decimal,
        now: datetime,
        chains: Sequence[Chain] | None = None,
        existing: Sequence[Allocation] = (),
    ) -> AnalysisResult:
        allowed_chains = set(chains) if chains is not None else None

        def on_chain(o: Opportunity) -> bool:
            return (allowed_chains is None or o.chain in allowed_chains) and o.chain in inputs.gas

        opportunities = [o for o in inputs.opportunities if on_chain(o)]
        parking_opps = [o for o in inputs.parking if on_chain(o)]
        parking_apy = reference_parking_apy(parking_opps)
        state = MarketState(
            as_of=inputs.state.as_of,
            protocol_tvl_usd=inputs.state.protocol_tvl_usd,
            protocol_tvl_change_7d=inputs.state.protocol_tvl_change_7d,
            asset_usd_price=inputs.state.asset_usd_price,
            parking_apy=parking_apy,
        )
        rules = default_rules(
            whitelist=self.whitelist,
            catalog=self.catalog,
            state=state,
            risk=self.risk,
            allocation=self.allocation,
            strict_addresses=self.strict_addresses,
        )
        scorer = RiskScorer(rules, self.whitelist, self.risk)
        cost_model = self.cost_model(inputs)
        calc = NlyCalculator(cost_model)
        protocol_cap = capital_usd * pct(self.allocation.max_protocol_pct)
        liquidity_share = pct(self.allocation.max_liquidity_share_pct)

        errors: list[Decision] = []
        candidates: list[Candidate] = []
        for opp in opportunities:
            size = max(min(protocol_cap, opp.liquidity_usd * liquidity_share), Decimal(1))
            try:
                nly = calc.evaluate(opp, size, now)
            except NlyError as exc:
                errors.append(Decision(opp.key, DecisionAction.SKIP, f"nly: {exc}"))
                continue
            candidates.append(scorer.candidate(opp, nly, parking_apy))

        parking: list[Candidate] = []
        for opp in parking_opps:
            try:
                nly = calc.evaluate(opp, protocol_cap, now)
            except NlyError as exc:
                errors.append(Decision(opp.key, DecisionAction.SKIP, f"nly: {exc}"))
                continue
            parking.append(scorer.candidate(opp, nly, parking_apy))

        min_positions = {
            chain: cost_model.min_position_usd(chain, self.costs.max_gas_frac)
            for chain in inputs.gas
            if allowed_chains is None or chain in allowed_chains
        }

        def resize(cand: Candidate, new_size: Decimal) -> Candidate:
            return scorer.candidate(
                cand.opportunity, calc.evaluate(cand.opportunity, new_size, now), parking_apy
            )

        allocator = GreedyAllocator(self.allocation, min_positions, resize=resize, existing=existing)
        plan = allocator.allocate(candidates, parking, capital_usd, now)
        return AnalysisResult(
            candidates=tuple(candidates),
            parking=tuple(parking),
            plan=plan,
            parking_apy=parking_apy,
            min_positions=min_positions,
            errors=tuple(errors),
        )
