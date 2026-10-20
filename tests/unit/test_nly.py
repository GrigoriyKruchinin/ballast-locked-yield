from dataclasses import replace
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ballast.domain.models import CostEstimate
from ballast.nly.calculator import NlyCalculator, NlyError
from tests.factories import NOW, ZERO_COSTS, FixedCostModel, parking, pt

CAP = Decimal(10_000)


def calc(costs: CostEstimate = ZERO_COSTS) -> NlyCalculator:
    return NlyCalculator(FixedCostModel(costs))


def test_zero_cost_matches_closed_form() -> None:
    res = calc().evaluate(pt(price="0.98", days=73), CAP, NOW)
    expected_period = Decimal(1) / Decimal("0.98") - 1
    assert abs(res.net_return - expected_period) < Decimal("1e-20")
    assert abs(res.nly_apy - expected_period * 5) < Decimal("1e-20")
    assert res.net_payoff_usd == res.gross_payoff_usd


def test_costs_reduce_yield_exactly() -> None:
    costs = CostEstimate(Decimal(1), Decimal(1), Decimal(5), Decimal(3), Decimal(0))
    res = calc(costs).evaluate(pt(price="0.98", days=73), CAP, NOW)
    invested = CAP - 1 - 5 - 3
    expected_payoff = invested / Decimal("0.98") - 1
    assert abs(res.net_payoff_usd - expected_payoff) < Decimal("1e-20")
    assert res.nly_apy < res.gross_return * 5


def test_depegged_asset_no_repeg_profit() -> None:
    # Asset trades at 0.95: we pay 0.95 per asset unit and assume payoff at 0.95 (no re-peg gain).
    res = calc().evaluate(pt(price="0.98", days=73, asset_usd="0.95"), CAP, NOW)
    plain = calc().evaluate(pt(price="0.98", days=73), CAP, NOW)
    assert abs(res.net_return - plain.net_return) < Decimal("1e-20")


def test_premium_asset_capped_at_par() -> None:
    res = calc().evaluate(pt(price="0.98", days=73, asset_usd="1.01"), CAP, NOW)
    plain = calc().evaluate(pt(price="0.98", days=73), CAP, NOW)
    assert res.net_return < plain.net_return


def test_matured_rejected() -> None:
    with pytest.raises(NlyError, match="matured"):
        calc().evaluate(pt(days=0), CAP, NOW)


def test_bad_inputs_rejected() -> None:
    with pytest.raises(NlyError):
        calc().evaluate(pt(), Decimal(0), NOW)
    with pytest.raises(NlyError, match="non-positive"):
        calc().evaluate(pt(price="0"), CAP, NOW)


def test_parking_amortizes_costs() -> None:
    costs = CostEstimate(Decimal("0.5"), Decimal("0.5"), Decimal(0), Decimal(0), Decimal(0))
    res = calc(costs).evaluate(parking(apy="0.0365"), CAP, NOW)
    # 30 days at 3.65% = 0.3% gross = 30 USD, minus 1 USD gas
    assert res.gross_payoff_usd - CAP == Decimal(30)
    assert res.net_payoff_usd - CAP == Decimal(29)


def test_parking_without_rate() -> None:
    with pytest.raises(NlyError, match="without a rate"):
        calc().evaluate(replace(parking(), quoted_apy=None), CAP, NOW)


money = st.decimals(min_value=0, max_value=200, places=6)


@given(
    price=st.decimals(min_value="0.80", max_value="0.9999", places=4),
    days=st.integers(min_value=1, max_value=365),
    g1=money,
    g2=money,
    fee=money,
    s1=money,
    s2=money,
)
def test_costs_never_increase_yield(
    price: Decimal, days: int, g1: Decimal, g2: Decimal, fee: Decimal, s1: Decimal, s2: Decimal
) -> None:
    opp = pt(price=str(price), days=days)
    base = calc().evaluate(opp, CAP, NOW)
    costly = calc(CostEstimate(g1, g2, fee, s1, s2)).evaluate(opp, CAP, NOW)
    assert costly.nly_apy <= base.nly_apy
    assert costly.net_payoff_usd <= costly.gross_payoff_usd


@given(
    p1=st.decimals(min_value="0.80", max_value="0.9999", places=4),
    p2=st.decimals(min_value="0.80", max_value="0.9999", places=4),
    days=st.integers(min_value=1, max_value=365),
)
def test_higher_price_lower_yield(p1: Decimal, p2: Decimal, days: int) -> None:
    lo, hi = sorted((p1, p2))
    r_lo = calc().evaluate(pt(price=str(lo), days=days), CAP, NOW)
    r_hi = calc().evaluate(pt(price=str(hi), days=days), CAP, NOW)
    assert r_hi.nly_apy <= r_lo.nly_apy


@given(capital=st.decimals(min_value=1, max_value=10**7, places=2))
def test_zero_cost_yield_independent_of_size(capital: Decimal) -> None:
    a = calc().evaluate(pt(), capital, NOW)
    b = calc().evaluate(pt(), CAP, NOW)
    assert abs(a.nly_apy - b.nly_apy) < Decimal("1e-20")
