"""Narrow interfaces (ISP). Core logic depends only on these (DIP)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from ballast.domain.models import (
    AllocationPlan,
    Candidate,
    Chain,
    CostEstimate,
    Opportunity,
    RiskFinding,
    TxRequest,
    TxResult,
)


@runtime_checkable
class ILockedYieldSource(Protocol):
    """A source of opportunities whose payoff is known at entry."""

    @property
    def name(self) -> str: ...

    async def fetch(self) -> Sequence[Opportunity]:
        """Return current opportunities. Must not raise on partial upstream failure."""
        ...


@runtime_checkable
class IChainClient(Protocol):
    @property
    def chain(self) -> Chain: ...

    async def block_number(self) -> int: ...

    async def gas_price_wei(self) -> int: ...

    async def call(self, to: str, data: str, block: int | None = None) -> bytes: ...


@runtime_checkable
class IPriceOracle(Protocol):
    async def usd_price(self, chain: Chain, token_address: str) -> Decimal | None: ...


@runtime_checkable
class IRiskRule(Protocol):
    @property
    def name(self) -> str: ...

    def evaluate(self, opportunity: Opportunity) -> RiskFinding: ...


@runtime_checkable
class ICostModel(Protocol):
    def estimate(self, opportunity: Opportunity, capital_usd: Decimal) -> CostEstimate: ...


@runtime_checkable
class IAllocator(Protocol):
    def allocate(
        self,
        candidates: Sequence[Candidate],
        parking: Sequence[Candidate],
        capital_usd: Decimal,
        now: datetime,
    ) -> AllocationPlan: ...


@runtime_checkable
class ITxExecutor(Protocol):
    """Executes a transaction in a simulated environment (e.g. an Anvil fork)."""

    @property
    def name(self) -> str: ...

    async def execute(self, request: TxRequest) -> TxResult: ...
