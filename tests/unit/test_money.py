from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ballast.domain.money import (
    TokenAmount,
    annualize_compound,
    annualize_simple,
    monthly_simple,
    pct,
    round_down,
    round_up,
    to_decimal,
)


def test_to_decimal_rejects_float_and_bool() -> None:
    with pytest.raises(TypeError):
        to_decimal(0.1)
    with pytest.raises(TypeError):
        to_decimal(True)
    assert to_decimal("0.1") == Decimal("0.1")
    assert to_decimal(3) == Decimal(3)


def test_pct() -> None:
    assert pct(35) == Decimal("0.35")
    assert pct("0.3") == Decimal("0.003")


def test_rounding_is_conservative() -> None:
    assert round_down(Decimal("1.0000019")) == Decimal("1.000001")
    assert round_up(Decimal("1.0000011")) == Decimal("1.000002")
    assert round_down(Decimal("-1.0000011")) == Decimal("-1.000002")


@given(raw=st.integers(min_value=0, max_value=10**40), decimals=st.integers(min_value=0, max_value=24))
def test_raw_roundtrip(raw: int, decimals: int) -> None:
    amount = TokenAmount.from_raw(raw, decimals)
    assert amount.to_raw(receive=True) == raw
    assert amount.to_raw(receive=False) == raw


@given(
    value=st.decimals(min_value=0, max_value=10**12, places=30, allow_nan=False, allow_infinity=False),
    decimals=st.integers(min_value=0, max_value=18),
)
def test_receive_rounds_down_pay_rounds_up(value: Decimal, decimals: int) -> None:
    amount = TokenAmount(value, decimals)
    down = amount.to_raw(receive=True)
    up = amount.to_raw(receive=False)
    assert down <= up <= down + 1
    assert Decimal(down).scaleb(-decimals) <= value <= Decimal(up).scaleb(-decimals)


def test_amount_decimals_mismatch() -> None:
    with pytest.raises(ValueError, match="different decimals"):
        _ = TokenAmount(Decimal(1), 6) + TokenAmount(Decimal(1), 18)


def test_amount_validates_decimals() -> None:
    with pytest.raises(ValueError, match="decimals"):
        TokenAmount(Decimal(1), 40)


def test_annualization() -> None:
    assert annualize_simple(Decimal("0.01"), Decimal("36.5")) == Decimal("0.1")
    assert monthly_simple(Decimal("0.01"), Decimal("30.4375")) == Decimal("0.01")
    compound = annualize_compound(Decimal("0.01"), Decimal(365))
    assert abs(compound - Decimal("0.01")) < Decimal("1e-30")
    assert annualize_compound(Decimal(-2), Decimal(10)) == Decimal(-1)
    with pytest.raises(ValueError):
        annualize_simple(Decimal(1), Decimal(0))


@given(
    r=st.decimals(min_value="-0.5", max_value="0.5", places=8),
    days=st.decimals(min_value=1, max_value=365, places=4),
)
def test_compound_vs_simple(r: Decimal, days: Decimal) -> None:
    simple = annualize_simple(r, days)
    compound = annualize_compound(r, days)
    # For terms under a year, compounding a positive return gives a higher APY.
    if r > 0:
        assert compound >= simple - Decimal("1e-20")
