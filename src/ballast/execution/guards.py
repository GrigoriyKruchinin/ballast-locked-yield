"""Transaction guards shared by every executor (fork simulation now, live chain later).

A request is rejected unless: the target contract is whitelisted for the chain, any approval
is for a whitelisted token with an exact (finite, non-zero) amount, ``minOut`` is set, the
recipient is our own account, and the USD value is within the per-transaction limit.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from ballast.domain.models import Chain, TxRequest

UNLIMITED_APPROVAL_FLOOR = 2**128  # anything this large is treated as an "infinite" approval


class GuardError(ValueError):
    """The transaction violates a safety rule and must not be sent or simulated."""


@dataclass(frozen=True, slots=True)
class TxGuard:
    targets: Mapping[Chain, frozenset[str]]
    tokens: Mapping[Chain, frozenset[str]]
    own_account: str
    max_tx_usd: Decimal

    def check(self, req: TxRequest) -> None:
        allowed = {a.lower() for a in self.targets.get(req.chain, frozenset())}
        if req.to.lower() not in allowed:
            msg = f"target {req.to} not whitelisted on {req.chain.slug}"
            raise GuardError(msg)
        if req.value_usd > self.max_tx_usd:
            msg = f"value {req.value_usd} USD exceeds per-tx limit {self.max_tx_usd}"
            raise GuardError(msg)
        if req.min_out_raw is None or req.min_out_raw <= 0:
            msg = "minOut must be set"
            raise GuardError(msg)
        if req.recipient is None or req.recipient.lower() != self.own_account.lower():
            msg = f"recipient {req.recipient} is not our account"
            raise GuardError(msg)
        if self.own_account.lower().removeprefix("0x") not in req.data.lower():
            msg = "calldata does not reference our account as receiver"
            raise GuardError(msg)
        if req.approve_token is not None:
            tokens = {t.lower() for t in self.tokens.get(req.chain, frozenset())}
            if req.approve_token.lower() not in tokens:
                msg = f"approval token {req.approve_token} not whitelisted"
                raise GuardError(msg)
            amount = req.approve_amount_raw
            if amount is None or amount <= 0 or amount >= UNLIMITED_APPROVAL_FLOOR:
                msg = "approval must be an exact, finite amount"
                raise GuardError(msg)
