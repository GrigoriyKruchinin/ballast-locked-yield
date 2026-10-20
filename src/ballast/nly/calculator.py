"""Net Locked Yield (NLY) calculator.

For a dated opportunity (e.g. Pendle PT) bought now and held to maturity::

    invested   = capital - gas_entry - fees - slippage_entry
    units      = invested / (entry_price * asset_usd)
    payoff     = units * payoff_per_unit * payoff_usd_per_asset - slippage_exit - gas_exit
    NLY (APY)  = (payoff - capital) / capital * 365 / days

``payoff_usd_per_asset`` is ``min(asset_usd_price, 1)``: we never count a re-peg as profit,
and an asset trading above par is assumed to redeem at par.

For parking (no maturity) NLY is computed over ``parking_horizon_days`` with the quoted
variable rate and the round-trip gas amortized over that horizon.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext

from ballast.domain.interfaces import ICostModel
from ballast.domain.models import CostEstimate, NlyResult, Opportunity
from ballast.domain.money import (
    DAYS_PER_YEAR,
    ONE,
    ZERO,
    annualize_compound,
    annualize_simple,
    money_context,
    monthly_simple,
)


class NlyError(ValueError):
    """Raised when an opportunity cannot be evaluated (expired, bad prices, no rate)."""


@dataclass(frozen=True, slots=True)
class NlyCalculator:
    cost_model: ICostModel
    parking_horizon_days: Decimal = Decimal(30)

    def evaluate(self, opportunity: Opportunity, capital_usd: Decimal, now: datetime) -> NlyResult:
        if capital_usd <= 0:
            msg = "capital must be positive"
            raise NlyError(msg)
        costs = self.cost_model.estimate(opportunity, capital_usd)
        if opportunity.maturity is None:
            return self._parking(opportunity, capital_usd, costs)
        return self._dated(opportunity, capital_usd, costs, now)

    def _dated(self, opp: Opportunity, capital: Decimal, costs: CostEstimate, now: datetime) -> NlyResult:
        days = opp.days_to_maturity(now)
        if days is None or days <= 0:
            msg = f"{opp.key}: already matured"
            raise NlyError(msg)
        if opp.entry_price <= 0 or opp.asset_usd_price <= 0 or opp.payoff_per_unit <= 0:
            msg = f"{opp.key}: non-positive price"
            raise NlyError(msg)
        with localcontext(money_context()):
            unit_cost_usd = opp.entry_price * opp.asset_usd_price
            payoff_usd_per_asset = min(opp.asset_usd_price, ONE)
            gross_units = capital / unit_cost_usd
            gross_payoff = gross_units * opp.payoff_per_unit * payoff_usd_per_asset

            invested = capital - costs.gas_entry_usd - costs.fees_usd - costs.slippage_entry_usd
            net_units = max(invested, ZERO) / unit_cost_usd
            net_payoff = (
                net_units * opp.payoff_per_unit * payoff_usd_per_asset
                - costs.slippage_exit_usd
                - costs.gas_exit_usd
            )
            return self._result(
                opp=opp,
                capital=capital,
                days=days,
                gross_payoff=gross_payoff,
                net_payoff=net_payoff,
                costs=costs,
            )

    def _parking(self, opp: Opportunity, capital: Decimal, costs: CostEstimate) -> NlyResult:
        if opp.quoted_apy is None:
            msg = f"{opp.key}: parking opportunity without a rate"
            raise NlyError(msg)
        days = self.parking_horizon_days
        with localcontext(money_context()):
            gross_payoff = capital * (ONE + opp.quoted_apy * days / DAYS_PER_YEAR)
            net_payoff = gross_payoff - costs.total_usd
            return self._result(
                opp=opp,
                capital=capital,
                days=days,
                gross_payoff=gross_payoff,
                net_payoff=net_payoff,
                costs=costs,
            )

    @staticmethod
    def _result(
        *,
        opp: Opportunity,
        capital: Decimal,
        days: Decimal,
        gross_payoff: Decimal,
        net_payoff: Decimal,
        costs: CostEstimate,
    ) -> NlyResult:
        with localcontext(money_context()):
            gross_return = (gross_payoff - capital) / capital
            net_return = (net_payoff - capital) / capital
            return NlyResult(
                opportunity_key=opp.key,
                capital_usd=capital,
                days=days,
                gross_payoff_usd=gross_payoff,
                net_payoff_usd=net_payoff,
                costs=costs,
                gross_return=gross_return,
                net_return=net_return,
                nly_apy=annualize_simple(net_return, days),
                nly_monthly=monthly_simple(net_return, days),
                nly_apy_compound=annualize_compound(net_return, days),
            )
