from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ballast.costs.model import GasSnapshot, LiquidityImpactSlippage, QuotedSlippage, RoundTripCostModel
from ballast.domain.models import Chain, SourceKind
from tests.factories import parking, pt

GAS = GasSnapshot(
    Chain.ARBITRUM, gas_price_wei=10_000_000, native_usd=Decimal(3000), l1_fee_usd_per_tx=Decimal("0.02")
)


def model(slippage: LiquidityImpactSlippage | QuotedSlippage | None = None) -> RoundTripCostModel:
    return RoundTripCostModel(
        gas={Chain.ARBITRUM: GAS},
        slippage=slippage or LiquidityImpactSlippage(),
        entry_gas_units=1_000_000,
        exit_gas_units=500_000,
        swap_fee_bps=Decimal(5),
        venue_fee_bps={SourceKind.PENDLE_PT: Decimal(2)},
    )


def test_gas_cost() -> None:
    # 1e6 * 1e7 wei = 1e13 wei = 1e-5 ETH = 0.03 USD, + 0.02 L1
    assert GAS.tx_cost_usd(1_000_000) == Decimal("0.05")


def test_usdc_has_no_swap_legs() -> None:
    est = model().estimate(pt(asset="USDC"), Decimal(10_000))
    assert est.fees_usd == Decimal(2)  # venue fee only
    assert est.slippage_exit_usd == 0
    assert est.gas_entry_usd == Decimal("0.05")
    assert est.gas_exit_usd == Decimal("0.035")


def test_non_base_asset_pays_two_swap_legs() -> None:
    est = model().estimate(pt(asset="USDe", protocol="ethena"), Decimal(10_000))
    assert est.fees_usd == Decimal(12)  # 2 legs * 5 bps + 2 bps
    assert est.slippage_exit_usd == Decimal(5)


def test_liquidity_slippage() -> None:
    slip = LiquidityImpactSlippage(base_bps=Decimal(5), impact_coef=Decimal("0.5"))
    assert slip.slippage_frac(pt(liquidity="1000000"), Decimal(10_000)) == Decimal("0.0055")
    assert slip.slippage_frac(pt(liquidity="0"), Decimal(10_000)) == slip.max_frac


@given(
    a=st.decimals(min_value=1, max_value=10**6, places=2),
    b=st.decimals(min_value=1, max_value=10**6, places=2),
)
def test_slippage_monotone_in_size(a: Decimal, b: Decimal) -> None:
    slip = LiquidityImpactSlippage()
    lo, hi = sorted((a, b))
    assert slip.slippage_frac(pt(), lo) <= slip.slippage_frac(pt(), hi)


def test_quoted_slippage_with_fallback() -> None:
    opp = pt()
    quoted = QuotedSlippage(
        {opp.key: {Decimal(1000): Decimal("0.001"), Decimal(5000): Decimal("0.003")}},
        fallback=LiquidityImpactSlippage(),
    )
    assert quoted.slippage_frac(opp, Decimal(800)) == Decimal("0.001")
    assert quoted.slippage_frac(opp, Decimal(4000)) == Decimal("0.003")
    assert quoted.slippage_frac(opp, Decimal(9000)) == LiquidityImpactSlippage().slippage_frac(
        opp, Decimal(9000)
    )


def test_min_position() -> None:
    m = model()
    # entry 0.05 + exit 0.035 = 0.085 USD; / 0.003 = 28.33 -> 29
    assert m.min_position_usd(Chain.ARBITRUM, Decimal("0.003")) == Decimal(29)
    with pytest.raises(ValueError):
        m.min_position_usd(Chain.ARBITRUM, Decimal(0))


def test_missing_gas_snapshot() -> None:
    with pytest.raises(LookupError):
        model().estimate(pt(chain=Chain.BASE), Decimal(1000))


def test_parking_uses_lending_gas_units() -> None:
    est = model().estimate(parking(), Decimal(10_000))
    # 250k gas * 1e7 wei * 3000 / 1e18 = 0.0075 + 0.02 L1
    assert est.gas_entry_usd == Decimal("0.0275")
    assert est.fees_usd == 0
