from dataclasses import replace
from decimal import Decimal

import pytest

from ballast.domain.models import Chain, TxRequest
from ballast.execution.guards import GuardError, TxGuard
from ballast.pipeline.simulate import SimTarget, model_pt_out

ROUTER = "0x888888888889758F76e7103c6CbF23ABbF58F946"
ME = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
GUARD = TxGuard(
    targets={Chain.ETHEREUM: frozenset({ROUTER})},
    tokens={Chain.ETHEREUM: frozenset({USDC})},
    own_account=ME,
    max_tx_usd=Decimal(2000),
)
OK = TxRequest(
    chain=Chain.ETHEREUM,
    to=ROUTER.lower(),
    data="0xc81f847a" + "00" * 12 + ME[2:].lower(),
    value_wei=0,
    value_usd=Decimal(1000),
    description="entry",
    min_out_raw=1,
    approve_token=USDC,
    approve_amount_raw=1_000_000_000,
    recipient=ME,
)


def test_valid_request_passes() -> None:
    GUARD.check(OK)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"to": "0x000000000000000000000000000000000000dEaD"}, "not whitelisted"),
        ({"chain": Chain.BASE}, "not whitelisted"),
        ({"value_usd": Decimal(5000)}, "per-tx limit"),
        ({"min_out_raw": None}, "minOut"),
        ({"min_out_raw": 0}, "minOut"),
        ({"recipient": "0x000000000000000000000000000000000000bEEF"}, "not our account"),
        ({"data": "0xc81f847a" + "00" * 64}, "receiver"),
        ({"approve_token": "0x000000000000000000000000000000000000dEaD"}, "approval token"),
        ({"approve_amount_raw": 2**256 - 1}, "exact, finite"),
        ({"approve_amount_raw": 0}, "exact, finite"),
    ],
)
def test_unsafe_requests_rejected(change: dict[str, object], message: str) -> None:
    with pytest.raises(GuardError, match=message):
        GUARD.check(replace(OK, **change))  # type: ignore[arg-type]


def test_model_pt_out_uses_fee_rate() -> None:
    target = SimTarget(
        "k", Chain.ETHEREUM, USDC, Decimal(1), "0xpt", Decimal("0.99"), Decimal("0.00365"), Decimal(100)
    )
    # fee = 0.00365 * 100/365 = 0.001; slippage 0.0001 -> 1000 * 0.9989 / 0.99
    assert model_pt_out(target, Decimal(1000), Decimal("0.0001")) == Decimal(1000) * Decimal(
        "0.9989"
    ) / Decimal("0.99")
