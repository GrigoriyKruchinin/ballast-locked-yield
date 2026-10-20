"""Paper portfolio: simulated capital following live allocation decisions.

Pure state machine; persistence lives in :mod:`ballast.portfolio.store`.

Per step:
1. accrue parking balances at their latest rate (missing pool -> withdrawn to idle, paying exit gas);
2. redeem matured positions at ``units * payoff * min(peg, 1)`` minus expected exit costs;
3. value = idle + parking + locked book value (linear accrual towards the expected net payoff);
4. apply the new plan: open ENTER allocations funded from idle, then parking (lowest rate first);
   rebalance parking only when a target differs by more than ``rebalance_band`` (no churn).
Every moved dollar pays the cost model's gas, fees and slippage.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal, localcontext

from ballast.domain.models import (
    Allocation,
    AllocationPlan,
    Chain,
    CostEstimate,
    NlyResult,
    Opportunity,
    SourceKind,
)
from ballast.domain.money import DAYS_PER_YEAR, ONE, ZERO, money_context

Evaluate = Callable[[Opportunity, Decimal], NlyResult]
SECONDS_PER_DAY = Decimal(86400)


@dataclass(frozen=True, slots=True)
class LockedPosition:
    key: str
    kind: SourceKind
    chain: Chain
    asset_symbol: str
    protocol: str
    capital_usd: Decimal
    units: Decimal
    entry_price: Decimal
    payoff_per_unit: Decimal
    expected_apy: Decimal
    expected_payoff_usd: Decimal  # net of expected exit costs
    entry_costs_usd: Decimal
    expected_exit_costs_usd: Decimal
    exposures: frozenset[str]
    opened_at: datetime
    maturity: datetime

    def book_value(self, now: datetime) -> Decimal:
        total = Decimal((self.maturity - self.opened_at).total_seconds())
        elapsed = Decimal((min(now, self.maturity) - self.opened_at).total_seconds())
        start = self.capital_usd - self.entry_costs_usd
        if total <= 0:
            return self.expected_payoff_usd
        return start + (self.expected_payoff_usd - start) * elapsed / total


@dataclass(frozen=True, slots=True)
class ParkingBalance:
    key: str
    chain: Chain
    protocol: str
    balance_usd: Decimal
    apy: Decimal
    exposures: frozenset[str]


@dataclass(frozen=True, slots=True)
class ClosedPosition:
    position: LockedPosition
    closed_at: datetime
    payoff_usd: Decimal
    reason: str


@dataclass(slots=True)
class PaperState:
    name: str
    initial_capital_usd: Decimal
    started_at: datetime
    updated_at: datetime
    idle_usd: Decimal
    costs_paid_usd: Decimal = ZERO
    locked: list[LockedPosition] = field(default_factory=list)
    parking: dict[str, ParkingBalance] = field(default_factory=dict)
    closed: list[ClosedPosition] = field(default_factory=list)

    @classmethod
    def new(cls, name: str, capital: Decimal, now: datetime) -> PaperState:
        return cls(name=name, initial_capital_usd=capital, started_at=now, updated_at=now, idle_usd=capital)

    def locked_value(self, now: datetime) -> Decimal:
        return sum((p.book_value(now) for p in self.locked), ZERO)

    def parked_value(self) -> Decimal:
        return sum((p.balance_usd for p in self.parking.values()), ZERO)

    def total_value(self, now: datetime) -> Decimal:
        return self.idle_usd + self.parked_value() + self.locked_value(now)

    def existing_allocations(self) -> list[Allocation]:
        return [
            Allocation(p.key, p.capital_usd, p.kind, p.exposures, p.expected_apy, p.maturity)
            for p in self.locked
        ]


@dataclass(frozen=True, slots=True)
class StepReport:
    opened: tuple[LockedPosition, ...]
    closed: tuple[ClosedPosition, ...]
    parking_moves: int
    accrued_parking_usd: Decimal
    costs_usd: Decimal


@dataclass(frozen=True, slots=True)
class PaperEngine:
    evaluate: Evaluate
    parking_exit_cost: Callable[[Chain], Decimal]
    rebalance_band: Decimal = Decimal("0.05")
    # Stay in a held parking venue unless a target of the same protocol pays this much more (no churn).
    parking_rate_band: Decimal = Decimal("0.005")
    min_move_usd: Decimal = Decimal(200)

    def accrue_and_mature(
        self,
        state: PaperState,
        now: datetime,
        parking_rates: Mapping[str, Decimal],
        pegs: Mapping[str, Decimal],
        peg_symbol: Callable[[str], str],
    ) -> tuple[Decimal, list[ClosedPosition], Decimal]:
        """Steps 1-2. Returns (accrued parking, newly closed, costs)."""
        with localcontext(money_context()):
            dt_days = Decimal((now - state.updated_at).total_seconds()) / SECONDS_PER_DAY
            accrued = ZERO
            costs = ZERO
            for key, bal in list(state.parking.items()):
                rate = parking_rates.get(key)
                if rate is None:
                    exit_cost = self.parking_exit_cost(bal.chain)
                    state.idle_usd += bal.balance_usd - exit_cost
                    costs += exit_cost
                    del state.parking[key]
                    continue
                gain = bal.balance_usd * bal.apy * max(dt_days, ZERO) / DAYS_PER_YEAR
                accrued += gain
                state.parking[key] = replace(bal, balance_usd=bal.balance_usd + gain, apy=rate)

            closed: list[ClosedPosition] = []
            still_open: list[LockedPosition] = []
            for pos in state.locked:
                if pos.maturity > now:
                    still_open.append(pos)
                    continue
                peg = min(pegs.get(peg_symbol(pos.asset_symbol).lower(), ONE), ONE)
                payoff = pos.units * pos.payoff_per_unit * peg - pos.expected_exit_costs_usd
                state.idle_usd += payoff
                costs += pos.expected_exit_costs_usd
                closed.append(ClosedPosition(pos, now, payoff, "matured"))
            state.locked = still_open
            state.closed.extend(closed)
            state.costs_paid_usd += costs
            state.updated_at = now
            return accrued, closed, costs

    def apply_plan(
        self,
        state: PaperState,
        plan: AllocationPlan,
        opportunities: Mapping[str, Opportunity],
        now: datetime,
    ) -> tuple[list[LockedPosition], int, Decimal]:
        """Step 4. Returns (opened, parking moves, costs)."""
        opened: list[LockedPosition] = []
        costs = ZERO
        for alloc in plan.allocations:
            if alloc.kind is SourceKind.PARKING:
                continue
            opp = opportunities.get(alloc.opportunity_key)
            if (
                opp is None
                or opp.maturity is None
                or any(p.key == alloc.opportunity_key for p in state.locked)
            ):
                continue
            funded, fund_costs = self._fund(state, alloc.capital_usd)
            costs += fund_costs
            if funded <= 0:
                continue
            nly = self.evaluate(opp, funded)
            position = _open(opp, alloc, nly, funded, now)
            state.locked.append(position)
            costs += nly.costs.gas_entry_usd + nly.costs.fees_usd + nly.costs.slippage_entry_usd
            opened.append(position)

        moves, move_costs = self._rebalance_parking(state, plan)
        costs += move_costs
        state.costs_paid_usd += costs
        return opened, moves, costs

    def _fund(self, state: PaperState, amount: Decimal) -> tuple[Decimal, Decimal]:
        """Take ``amount`` from idle, then from the lowest-rate parking balances."""
        need = amount
        costs = ZERO
        take = min(state.idle_usd, need)
        state.idle_usd -= take
        need -= take
        for key in sorted(state.parking, key=lambda k: state.parking[k].apy):
            if need <= 0:
                break
            bal = state.parking[key]
            exit_cost = self.parking_exit_cost(bal.chain)
            take = min(bal.balance_usd - exit_cost, need)
            if take <= 0:
                continue
            costs += exit_cost
            remaining = bal.balance_usd - take - exit_cost
            if remaining < self.min_move_usd:
                state.idle_usd += max(remaining, ZERO)
                del state.parking[key]
            else:
                state.parking[key] = replace(bal, balance_usd=remaining)
            need -= take
        return amount - max(need, ZERO), costs

    def _sticky_targets(self, state: PaperState, plan: AllocationPlan) -> dict[str, Allocation]:
        """Replace a target by an already-held venue of the same protocol when the rate gain is small."""
        targets: dict[str, Allocation] = {}
        held = dict(state.parking)
        for alloc in (a for a in plan.allocations if a.kind is SourceKind.PARKING):
            if alloc.opportunity_key in held:
                targets[alloc.opportunity_key] = alloc
                held.pop(alloc.opportunity_key)
                continue
            protocols = {e for e in alloc.exposures if e.startswith("protocol:")}
            substitute = next(
                (
                    bal
                    for bal in held.values()
                    if protocols & set(bal.exposures)
                    and alloc.expected_apy - bal.apy < self.parking_rate_band
                ),
                None,
            )
            if substitute is None:
                targets[alloc.opportunity_key] = alloc
                continue
            held.pop(substitute.key)
            targets[substitute.key] = replace(
                alloc,
                opportunity_key=substitute.key,
                exposures=substitute.exposures,
                expected_apy=substitute.apy,
            )
        return targets

    def _rebalance_parking(self, state: PaperState, plan: AllocationPlan) -> tuple[int, Decimal]:
        targets = self._sticky_targets(state, plan)
        moves = 0
        costs = ZERO
        total_parking_target = sum((a.capital_usd for a in targets.values()), ZERO)
        band = max(total_parking_target * self.rebalance_band, self.min_move_usd)
        # withdraw from venues no longer targeted or far above target
        for key, bal in list(state.parking.items()):
            target = targets[key].capital_usd if key in targets else ZERO
            if bal.balance_usd - target > band:
                exit_cost = self.parking_exit_cost(bal.chain)
                excess = bal.balance_usd - target
                state.idle_usd += excess - exit_cost
                costs += exit_cost
                moves += 1
                if target <= 0:
                    del state.parking[key]
                else:
                    state.parking[key] = replace(bal, balance_usd=target)
        # deposit idle into targets below their level
        for key, alloc in sorted(targets.items(), key=lambda kv: kv[1].expected_apy, reverse=True):
            current = state.parking[key].balance_usd if key in state.parking else ZERO
            gap = min(alloc.capital_usd - current, state.idle_usd)
            if gap <= band and key in state.parking:
                continue
            if gap < self.min_move_usd:
                continue
            chain = _chain_of(key)
            entry_cost = self.parking_exit_cost(chain)
            deposit = gap - entry_cost
            state.idle_usd -= gap
            costs += entry_cost
            moves += 1
            prev = state.parking.get(key)
            state.parking[key] = ParkingBalance(
                key=key,
                chain=chain,
                protocol=key.split(":")[1],
                balance_usd=(prev.balance_usd if prev else ZERO) + deposit,
                apy=alloc.expected_apy,
                exposures=alloc.exposures,
            )
        return moves, costs


def _chain_of(key: str) -> Chain:
    """Parking keys look like ``parking:{protocol}:{chain}:{asset}:{pool}``."""
    parts = key.split(":")
    if len(parts) < 3:
        msg = f"malformed parking key {key!r}"
        raise ValueError(msg)
    return Chain.from_slug(parts[2])


def _open(
    opp: Opportunity, alloc: Allocation, nly: NlyResult, funded: Decimal, now: datetime
) -> LockedPosition:
    costs: CostEstimate = nly.costs
    with localcontext(money_context()):
        entry_costs = costs.gas_entry_usd + costs.fees_usd + costs.slippage_entry_usd
        unit_cost = opp.entry_price * opp.asset_usd_price
        units = (funded - entry_costs) / unit_cost
        exit_costs = costs.gas_exit_usd + costs.slippage_exit_usd
    if opp.maturity is None:
        msg = "locked position needs a maturity"
        raise ValueError(msg)
    return LockedPosition(
        key=opp.key,
        kind=opp.kind,
        chain=opp.chain,
        asset_symbol=opp.asset_symbol,
        protocol=opp.protocol,
        capital_usd=funded,
        units=units,
        entry_price=opp.entry_price,
        payoff_per_unit=opp.payoff_per_unit,
        expected_apy=nly.nly_apy,
        expected_payoff_usd=nly.net_payoff_usd,
        entry_costs_usd=entry_costs,
        expected_exit_costs_usd=exit_costs,
        exposures=alloc.exposures,
        opened_at=now,
        maturity=opp.maturity,
    )
