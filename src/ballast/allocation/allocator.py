"""Greedy risk-adjusted allocator.

Order of operations:
1. Keep ``reserve_pct`` of capital out of locked positions (it goes to parking).
2. Rank eligible dated candidates (risk allowed and NLY >= threshold) by excess APY over threshold.
3. Size each candidate by the tightest of: free lockable capital, exposure caps
   (protocol, venue, tier B, tier-B asset), market liquidity share; then re-evaluate NLY at that
   size (costs are size dependent) and re-check the threshold and the minimum position.
4. Place the remainder in parking by net rate, respecting protocol caps; leftover is idle cash.

Every candidate produces a :class:`Decision` with a reason, so skipped trades are explainable.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from ballast.config.settings import AllocationSettings
from ballast.domain.models import (
    Allocation,
    AllocationPlan,
    Candidate,
    Chain,
    Decision,
    DecisionAction,
    SourceKind,
)
from ballast.domain.money import ZERO, pct, round_down

Resizer = Callable[[Candidate, Decimal], Candidate]
MIN_PARKING_USD = Decimal(50)


@dataclass(frozen=True, slots=True)
class ExposureLimits:
    protocol_frac: Decimal
    venue_frac: Decimal
    tier_b_frac: Decimal
    tier_b_asset_frac: Decimal

    @classmethod
    def from_settings(cls, s: AllocationSettings) -> ExposureLimits:
        return cls(
            protocol_frac=pct(s.max_protocol_pct),
            venue_frac=pct(s.max_venue_pct),
            tier_b_frac=pct(s.max_tier_b_pct),
            tier_b_asset_frac=pct(s.max_tier_b_asset_pct),
        )

    def cap_frac(self, key: str, exposures: frozenset[str]) -> Decimal | None:
        if key.startswith("protocol:"):
            return self.protocol_frac
        if key.startswith("venue:"):
            return self.venue_frac
        if key == "tier:B":
            return self.tier_b_frac
        if key.startswith("asset:") and "tier:B" in exposures:
            return self.tier_b_asset_frac
        return None


@dataclass(slots=True)
class _Book:
    capital: Decimal
    limits: ExposureLimits
    used: defaultdict[str, Decimal] = field(default_factory=lambda: defaultdict(lambda: ZERO))

    def room(self, exposures: frozenset[str]) -> tuple[Decimal, str | None]:
        """Smallest remaining room across capped exposures, and the binding key."""
        best: Decimal | None = None
        binding: str | None = None
        for key in sorted(exposures):
            frac = self.limits.cap_frac(key, exposures)
            if frac is None:
                continue
            room = self.capital * frac - self.used[key]
            if best is None or room < best:
                best, binding = room, key
        return (best if best is not None else self.capital), binding

    def add(self, exposures: frozenset[str], amount: Decimal) -> None:
        for key in exposures:
            self.used[key] += amount


@dataclass(frozen=True, slots=True)
class GreedyAllocator:
    """Implements :class:`ballast.domain.interfaces.IAllocator`."""

    settings: AllocationSettings
    min_position_usd: Mapping[Chain, Decimal]
    resize: Resizer | None = None
    existing: Sequence[Allocation] = ()

    def allocate(
        self,
        candidates: Sequence[Candidate],
        parking: Sequence[Candidate],
        capital_usd: Decimal,
        now: datetime,
    ) -> AllocationPlan:
        limits = ExposureLimits.from_settings(self.settings)
        book = _Book(capital_usd, limits)
        locked_existing = ZERO
        for held in self.existing:
            if held.kind is not SourceKind.PARKING:
                book.add(held.exposures, held.capital_usd)
                locked_existing += held.capital_usd
        reserve = capital_usd * pct(self.settings.reserve_pct)
        lockable = max(capital_usd - reserve - locked_existing, ZERO)

        allocations: list[Allocation] = []
        decisions: list[Decision] = []
        for cand in sorted(candidates, key=lambda c: c.excess_apy, reverse=True):
            decision, alloc = self._consider(cand, book, lockable, now)
            decisions.append(decision)
            if alloc is not None:
                allocations.append(alloc)
                book.add(alloc.exposures, alloc.capital_usd)
                lockable -= alloc.capital_usd

        free = capital_usd - locked_existing - sum((a.capital_usd for a in allocations), ZERO)
        park_allocs, park_decisions, idle = self._park(parking, book, free)
        return AllocationPlan(
            allocations=tuple(allocations + park_allocs),
            decisions=tuple(decisions + park_decisions),
            idle_usd=idle,
        )

    def _consider(
        self, cand: Candidate, book: _Book, lockable: Decimal, now: datetime
    ) -> tuple[Decision, Allocation | None]:
        opp = cand.opportunity

        def skip(reason: str, c: Candidate = cand) -> tuple[Decision, None]:
            return (
                Decision(
                    opp.key,
                    DecisionAction.SKIP,
                    reason,
                    nly_apy=c.nly.nly_apy,
                    threshold_apy=c.threshold_apy,
                ),
                None,
            )

        if not cand.risk.allowed:
            return skip("risk: " + "; ".join(cand.risk.reasons[:3]))
        if cand.nly.nly_apy < cand.threshold_apy:
            return skip(f"NLY {cand.nly.nly_apy:.2%} below threshold {cand.threshold_apy:.2%}")
        min_size = self.min_position_usd.get(opp.chain)
        if min_size is None:
            return skip(f"no minimum position for {opp.chain.slug} (gas unknown)")

        room, binding = book.room(cand.exposures)
        liquidity_cap = opp.liquidity_usd * pct(self.settings.max_liquidity_share_pct)
        size = round_down(min(lockable, room, liquidity_cap), Decimal(1))
        if size < min_size:
            limiter = "free capital"
            if size == round_down(room, Decimal(1)) and binding:
                limiter = f"cap {binding}"
            elif size == round_down(liquidity_cap, Decimal(1)):
                limiter = "market liquidity"
            return skip(f"size {size} < min position {min_size} (limited by {limiter})")

        if self.resize is not None:
            cand = self.resize(cand, size)
            if not cand.risk.allowed or cand.nly.nly_apy < cand.threshold_apy:
                return skip(
                    f"at size {size}: NLY {cand.nly.nly_apy:.2%} below threshold {cand.threshold_apy:.2%}",
                    cand,
                )

        alloc = Allocation(
            opportunity_key=opp.key,
            capital_usd=size,
            kind=opp.kind,
            exposures=cand.exposures,
            expected_apy=cand.nly.nly_apy,
            maturity=opp.maturity,
        )
        decision = Decision(
            opp.key,
            DecisionAction.ENTER,
            f"excess {cand.excess_apy:.2%} over threshold",
            capital_usd=size,
            nly_apy=cand.nly.nly_apy,
            threshold_apy=cand.threshold_apy,
        )
        return decision, alloc

    def _park(
        self, parking: Sequence[Candidate], book: _Book, free: Decimal
    ) -> tuple[list[Allocation], list[Decision], Decimal]:
        allocations: list[Allocation] = []
        decisions: list[Decision] = []
        remaining = free
        for cand in sorted(parking, key=lambda c: c.nly.nly_apy, reverse=True):
            key = cand.opportunity.key
            if remaining < MIN_PARKING_USD:
                break
            if not cand.risk.allowed:
                reason = "risk: " + "; ".join(cand.risk.reasons[:3])
                decisions.append(Decision(key, DecisionAction.SKIP, reason))
                continue
            room, _ = book.room(cand.exposures)
            size = round_down(min(remaining, room), Decimal(1))
            if size < MIN_PARKING_USD:
                decisions.append(Decision(key, DecisionAction.SKIP, "parking cap reached"))
                continue
            allocations.append(
                Allocation(key, size, SourceKind.PARKING, cand.exposures, cand.nly.nly_apy, None)
            )
            decisions.append(Decision(key, DecisionAction.PARK, "parking", size, cand.nly.nly_apy))
            book.add(cand.exposures, size)
            remaining -= size
        return allocations, decisions, remaining
