"""Cost model: gas, fees and slippage of a round trip (enter now, exit at maturity).

Inputs that change over time (gas prices, native token price, quoted price impact)
are injected as snapshots so the model itself stays pure and deterministic.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Protocol

from ballast.domain.models import Chain, CostEstimate, Opportunity, SourceKind
from ballast.domain.money import ONE, ZERO, money_context, round_up

BPS = Decimal(10_000)
WEI_PER_NATIVE = Decimal(10) ** 18
# Assets that need no extra swap leg from/to the base stablecoin we hold.
BASE_STABLES = frozenset({"USDC"})


@dataclass(frozen=True, slots=True)
class GasSnapshot:
    """Gas pricing on one chain at one moment."""

    chain: Chain
    gas_price_wei: int
    native_usd: Decimal
    l1_fee_usd_per_tx: Decimal = ZERO

    def tx_cost_usd(self, gas_units: int) -> Decimal:
        with localcontext(money_context()):
            l2 = Decimal(gas_units) * Decimal(self.gas_price_wei) * self.native_usd / WEI_PER_NATIVE
            return l2 + self.l1_fee_usd_per_tx


class ISlippageModel(Protocol):
    def slippage_frac(self, opportunity: Opportunity, capital_usd: Decimal) -> Decimal:
        """Expected price impact of entering ``capital_usd`` as a fraction of notional."""
        ...


@dataclass(frozen=True, slots=True)
class LiquidityImpactSlippage:
    """Heuristic: ``base_bps + impact_coef * size / liquidity``.

    Conservative fallback when no live quote exists; calibrated later against
    Pendle quotes and fork simulations.
    """

    base_bps: Decimal = Decimal(5)
    impact_coef: Decimal = Decimal("0.5")
    max_frac: Decimal = Decimal("0.05")

    def slippage_frac(self, opportunity: Opportunity, capital_usd: Decimal) -> Decimal:
        with localcontext(money_context()):
            if opportunity.liquidity_usd <= 0:
                return self.max_frac
            impact = self.impact_coef * capital_usd / opportunity.liquidity_usd
            return min(self.base_bps / BPS + impact, self.max_frac)


@dataclass(frozen=True, slots=True)
class QuotedSlippage:
    """Uses pre-fetched quotes ``{opportunity_key: {size_usd: impact_frac}}``.

    Picks the smallest quoted size >= requested capital; otherwise falls back.
    """

    quotes: Mapping[str, Mapping[Decimal, Decimal]]
    fallback: ISlippageModel

    def slippage_frac(self, opportunity: Opportunity, capital_usd: Decimal) -> Decimal:
        by_size = self.quotes.get(opportunity.key)
        if by_size:
            sizes = sorted(s for s in by_size if s >= capital_usd)
            if sizes:
                return max(by_size[sizes[0]], ZERO)
        return self.fallback.slippage_frac(opportunity, capital_usd)


@dataclass(frozen=True, slots=True)
class RoundTripCostModel:
    """Implements :class:`ballast.domain.interfaces.ICostModel`.

    - gas: entry and exit transactions priced at the current gas snapshot
      (exit uses the same snapshot: the best available estimate at entry time);
    - fees: swap fee for the stable -> underlying leg on entry and the reverse leg on exit
      when the accounting asset is not a base stable, plus the venue fee;
    - slippage: entry impact from the slippage model; exit impact applies only when an
      extra swap leg is needed (PT redemption at maturity is at par, no AMM).
    """

    gas: Mapping[Chain, GasSnapshot]
    slippage: ISlippageModel
    entry_gas_units: int
    exit_gas_units: int
    swap_fee_bps: Decimal
    venue_fee_bps: Mapping[SourceKind, Decimal]
    exit_swap_slippage_bps: Decimal = Decimal(5)
    parking_gas_units: int = 250_000  # lending supply / withdraw

    def estimate(self, opportunity: Opportunity, capital_usd: Decimal) -> CostEstimate:
        snapshot = self.gas.get(opportunity.chain)
        if snapshot is None:
            msg = f"no gas snapshot for {opportunity.chain.slug}"
            raise LookupError(msg)
        with localcontext(money_context()):
            needs_swap = opportunity.asset_symbol.upper() not in BASE_STABLES
            legs = Decimal(2) if needs_swap else ZERO
            fees = capital_usd * (self.swap_fee_bps * legs + self._venue_fee_bps(opportunity)) / BPS
            slip_entry = capital_usd * self.slippage.slippage_frac(opportunity, capital_usd)
            slip_exit = capital_usd * self.exit_swap_slippage_bps / BPS if needs_swap else ZERO
            is_parking = opportunity.kind is SourceKind.PARKING
            entry_units = self.parking_gas_units if is_parking else self.entry_gas_units
            exit_units = self.parking_gas_units if is_parking else self.exit_gas_units
            return CostEstimate(
                gas_entry_usd=round_up(snapshot.tx_cost_usd(entry_units)),
                gas_exit_usd=round_up(snapshot.tx_cost_usd(exit_units)),
                fees_usd=round_up(fees),
                slippage_entry_usd=round_up(slip_entry),
                slippage_exit_usd=round_up(slip_exit),
            )

    def _venue_fee_bps(self, opportunity: Opportunity) -> Decimal:
        """Pendle charges ``feeRate`` in implied-rate terms: price cost ~= feeRate * days / 365."""
        fallback = self.venue_fee_bps.get(opportunity.kind, ZERO)
        raw = opportunity.extra.get("fee_rate")
        days = opportunity.days_to_maturity()
        if opportunity.kind is not SourceKind.PENDLE_PT or raw is None or days is None or days <= 0:
            return fallback
        try:
            fee_rate = Decimal(raw)
        except ArithmeticError:
            return fallback
        return max(fee_rate, ZERO) * days / Decimal(365) * BPS

    def min_position_usd(self, chain: Chain, max_gas_frac: Decimal) -> Decimal:
        """Smallest position whose entry + exit gas is within ``max_gas_frac`` of size."""
        snapshot = self.gas[chain]
        if max_gas_frac <= 0 or max_gas_frac >= ONE:
            msg = "max_gas_frac must be in (0, 1)"
            raise ValueError(msg)
        with localcontext(money_context()):
            gas = snapshot.tx_cost_usd(self.entry_gas_units) + snapshot.tx_cost_usd(self.exit_gas_units)
            return round_up(gas / max_gas_frac, Decimal(1))
