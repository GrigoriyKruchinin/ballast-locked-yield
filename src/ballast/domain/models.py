"""Core domain types shared by all components.

Domain objects are immutable. Persistence models live in :mod:`ballast.storage`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import IntEnum, StrEnum


class Chain(IntEnum):
    """Supported chains, valued by chain id."""

    ETHEREUM = 1
    ARBITRUM = 42161
    BASE = 8453

    @property
    def slug(self) -> str:
        return self.name.lower()

    @classmethod
    def from_slug(cls, slug: str) -> Chain:
        try:
            return cls[slug.strip().upper()]
        except KeyError as exc:
            msg = f"unsupported chain: {slug!r}"
            raise ValueError(msg) from exc


class Tier(StrEnum):
    """Asset risk tier. Tier C is never allowed."""

    A = "A"
    B = "B"
    C = "C"


class SourceKind(StrEnum):
    """Kinds of locked-yield sources."""

    PENDLE_PT = "pendle_pt"
    PARKING = "parking"
    FUTURES_BASIS = "futures_basis"
    BELOW_REDEMPTION = "below_redemption"


class DecisionAction(StrEnum):
    ENTER = "enter"
    SKIP = "skip"
    PARK = "park"
    EXIT = "exit"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Token:
    chain: Chain
    address: str
    symbol: str
    decimals: int


@dataclass(frozen=True, slots=True)
class Opportunity:
    """A normalized locked-yield opportunity observed at ``observed_at``.

    Prices are in units of the *accounting asset* (e.g. 1 PT-USDe redeems for 1 USDe at maturity).
    ``asset_usd_price`` converts accounting-asset units to USD at observation time.
    """

    key: str
    kind: SourceKind
    chain: Chain
    venue: str
    protocol: str
    asset_symbol: str
    asset_address: str | None
    entry_price: Decimal
    payoff_per_unit: Decimal
    maturity: datetime | None
    observed_at: datetime
    liquidity_usd: Decimal
    asset_usd_price: Decimal = Decimal(1)
    quoted_apy: Decimal | None = None
    market_address: str | None = None
    extra: dict[str, str] = field(default_factory=dict)

    def days_to_maturity(self, at: datetime | None = None) -> Decimal | None:
        if self.maturity is None:
            return None
        ref = at or self.observed_at
        seconds = Decimal((self.maturity - ref).total_seconds())
        return seconds / Decimal(86400)


@dataclass(frozen=True, slots=True)
class CostEstimate:
    """All costs of a round trip (enter now, exit at maturity), in USD."""

    gas_entry_usd: Decimal
    gas_exit_usd: Decimal
    fees_usd: Decimal
    slippage_entry_usd: Decimal
    slippage_exit_usd: Decimal

    @property
    def gas_usd(self) -> Decimal:
        return self.gas_entry_usd + self.gas_exit_usd

    @property
    def slippage_usd(self) -> Decimal:
        return self.slippage_entry_usd + self.slippage_exit_usd

    @property
    def total_usd(self) -> Decimal:
        return self.gas_usd + self.fees_usd + self.slippage_usd


@dataclass(frozen=True, slots=True)
class NlyResult:
    """Net Locked Yield of one opportunity for a given capital size."""

    opportunity_key: str
    capital_usd: Decimal
    days: Decimal
    gross_payoff_usd: Decimal
    net_payoff_usd: Decimal
    costs: CostEstimate
    gross_return: Decimal
    net_return: Decimal
    nly_apy: Decimal
    nly_monthly: Decimal
    nly_apy_compound: Decimal

    @property
    def cost_share_of_gross(self) -> Decimal | None:
        gross_profit = self.gross_payoff_usd - self.capital_usd
        if gross_profit <= 0:
            return None
        return self.costs.total_usd / gross_profit


@dataclass(frozen=True, slots=True)
class RiskFinding:
    rule: str
    score: Decimal
    reason: str
    blocking: bool = False


@dataclass(frozen=True, slots=True)
class RiskScore:
    """Aggregated risk of an opportunity. ``score`` is 0 (best) to 100 (worst)."""

    tier: Tier
    score: Decimal
    findings: tuple[RiskFinding, ...]
    allowed: bool

    @property
    def reasons(self) -> list[str]:
        return [f.reason for f in self.findings if f.blocking or f.score > 0]


@dataclass(frozen=True, slots=True)
class Candidate:
    """An opportunity enriched with NLY, risk and its entry threshold."""

    opportunity: Opportunity
    nly: NlyResult
    risk: RiskScore
    threshold_apy: Decimal
    exposures: frozenset[str]

    @property
    def excess_apy(self) -> Decimal:
        return self.nly.nly_apy - self.threshold_apy


@dataclass(frozen=True, slots=True)
class Allocation:
    opportunity_key: str
    capital_usd: Decimal
    kind: SourceKind
    exposures: frozenset[str]
    expected_apy: Decimal
    maturity: datetime | None


@dataclass(frozen=True, slots=True)
class Decision:
    opportunity_key: str
    action: DecisionAction
    reason: str
    capital_usd: Decimal = Decimal(0)
    nly_apy: Decimal | None = None
    threshold_apy: Decimal | None = None


@dataclass(frozen=True, slots=True)
class AllocationPlan:
    allocations: tuple[Allocation, ...]
    decisions: tuple[Decision, ...]
    idle_usd: Decimal

    @property
    def locked_usd(self) -> Decimal:
        return sum(
            (a.capital_usd for a in self.allocations if a.kind is not SourceKind.PARKING),
            Decimal(0),
        )

    @property
    def parked_usd(self) -> Decimal:
        return sum(
            (a.capital_usd for a in self.allocations if a.kind is SourceKind.PARKING),
            Decimal(0),
        )


@dataclass(frozen=True, slots=True)
class TxRequest:
    """A transaction to execute or simulate. ``value_usd`` drives per-tx limits."""

    chain: Chain
    to: str
    data: str
    value_wei: int
    value_usd: Decimal
    description: str
    min_out_raw: int | None = None
    approve_token: str | None = None
    approve_amount_raw: int | None = None
    recipient: str | None = None


@dataclass(frozen=True, slots=True)
class TxResult:
    success: bool
    gas_used: int
    gas_cost_usd: Decimal
    amount_out_raw: int | None
    tx_hash: str | None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class MonitorEvent:
    kind: str
    severity: Severity
    subject: str
    message: str
    value: Decimal | None = None
    observed_at: datetime = field(default_factory=utcnow)
