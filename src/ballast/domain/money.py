"""Decimal money primitives.

All money and on-chain amounts on the decision path use :class:`decimal.Decimal`.
Rounding is explicit; helpers that produce amounts we *receive* round down and
helpers that produce amounts we *pay* round up, so estimates are conservative.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Context, Decimal, localcontext
from typing import Final

PRECISION: Final = 50
DAYS_PER_YEAR: Final = Decimal(365)
DAYS_PER_MONTH: Final = Decimal("30.4375")  # 365.25 / 12
ZERO: Final = Decimal(0)
ONE: Final = Decimal(1)
HUNDRED: Final = Decimal(100)
USD_QUANTUM: Final = Decimal("0.000001")


def money_context() -> Context:
    """High-precision context used for every money computation."""
    return Context(prec=PRECISION)


def to_decimal(value: object) -> Decimal:
    """Convert a value to Decimal without passing through float.

    Floats are rejected: they carry binary rounding error into money math.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool | float):
        msg = f"{type(value).__name__} is not allowed in money math"
        raise TypeError(msg)
    if isinstance(value, int | str):
        return Decimal(value)
    msg = f"cannot convert {type(value).__name__} to Decimal"
    raise TypeError(msg)


def pct(value: Decimal | int | str) -> Decimal:
    """Percent units (35 means 35%) to a fraction (0.35)."""
    return to_decimal(value) / HUNDRED


def round_down(value: Decimal, quantum: Decimal = USD_QUANTUM) -> Decimal:
    """Round towards negative infinity: use for amounts we receive."""
    return value.quantize(quantum, rounding=ROUND_FLOOR)


def round_up(value: Decimal, quantum: Decimal = USD_QUANTUM) -> Decimal:
    """Round towards positive infinity: use for amounts we pay."""
    return value.quantize(quantum, rounding=ROUND_CEILING)


@dataclass(frozen=True, slots=True)
class TokenAmount:
    """A token amount in human units together with the token's decimals."""

    value: Decimal
    decimals: int

    def __post_init__(self) -> None:
        if not 0 <= self.decimals <= 36:
            msg = f"unsupported decimals: {self.decimals}"
            raise ValueError(msg)
        if not self.value.is_finite():
            msg = "amount must be finite"
            raise ValueError(msg)

    @classmethod
    def from_raw(cls, raw: int, decimals: int) -> TokenAmount:
        """Build from an on-chain integer amount."""
        with localcontext(money_context()):
            return cls(Decimal(raw).scaleb(-decimals), decimals)

    def to_raw(self, *, receive: bool) -> int:
        """Convert to an on-chain integer amount.

        ``receive=True`` rounds down (e.g. ``minOut``), ``receive=False`` rounds up (e.g. exact approve).
        """
        with localcontext(money_context()):
            scaled = self.value.scaleb(self.decimals)
            rounding = ROUND_FLOOR if receive else ROUND_CEILING
            return int(scaled.to_integral_value(rounding=rounding))

    def __add__(self, other: TokenAmount) -> TokenAmount:
        self._check(other)
        return TokenAmount(self.value + other.value, self.decimals)

    def __sub__(self, other: TokenAmount) -> TokenAmount:
        self._check(other)
        return TokenAmount(self.value - other.value, self.decimals)

    def _check(self, other: TokenAmount) -> None:
        if other.decimals != self.decimals:
            msg = "cannot combine amounts with different decimals"
            raise ValueError(msg)


def annualize_simple(period_return: Decimal, days: Decimal) -> Decimal:
    """Simple (non-compounded) annualization: r * 365 / days."""
    if days <= 0:
        msg = "days must be positive"
        raise ValueError(msg)
    with localcontext(money_context()):
        return period_return * DAYS_PER_YEAR / days


def monthly_simple(period_return: Decimal, days: Decimal) -> Decimal:
    """Simple per-month return: r * 30.4375 / days."""
    if days <= 0:
        msg = "days must be positive"
        raise ValueError(msg)
    with localcontext(money_context()):
        return period_return * DAYS_PER_MONTH / days


def annualize_compound(period_return: Decimal, days: Decimal) -> Decimal:
    """Compounded annualization: (1 + r) ** (365 / days) - 1."""
    if days <= 0:
        msg = "days must be positive"
        raise ValueError(msg)
    if period_return <= -ONE:
        return -ONE
    with localcontext(money_context()):
        exponent = DAYS_PER_YEAR / days
        return ((ONE + period_return).ln() * exponent).exp() - ONE
