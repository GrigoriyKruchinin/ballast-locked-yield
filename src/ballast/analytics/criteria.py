"""Go/No-Go criteria (docs/decisions.md) evaluated on validation metrics."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ballast.backtest.engine import BacktestMetrics

# Book-value accrual can push a capped exposure marginally above its cap between rebalances.
CAP_TOLERANCE = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class Criterion:
    name: str
    value: str
    threshold: str
    passed: bool | None  # None = not measurable from this data
    mandatory: bool = False


def _pct(v: Decimal | None, digits: int = 2) -> str:
    return "n/a" if v is None else f"{v * 100:.{digits}f}%"


def evaluate_criteria(m: BacktestMetrics, *, fork_within_tolerance: bool | None = None) -> list[Criterion]:
    return [
        Criterion(
            "Net yield on deposit (after costs and risk adjustment)",
            f"{_pct(m.net_apy_after_incidents * Decimal('30.4375') / 365, 3)} / month",
            ">= 0.500% / month",
            m.net_apy_after_incidents * Decimal("30.4375") / 365 >= Decimal("0.005"),
            mandatory=True,
        ),
        Criterion(
            "Excess over parking",
            f"{m.excess_pp:.2f} pp APY",
            ">= 3.00 pp APY",
            m.excess_pp >= 3,
        ),
        Criterion(
            "Weeks with >= 70% of capital in locked positions",
            f"{m.weeks_deployed_70}/{m.weeks_total} ({_pct(m.weeks_deployed_share, 0)})",
            ">= 70% of weeks",
            m.weeks_deployed_share >= Decimal("0.7") if m.weeks_total else None,
        ),
        Criterion(
            "Gas + fees + slippage share of gross yield",
            _pct(m.cost_share_of_gross, 1),
            "<= 15%",
            None if m.cost_share_of_gross is None else m.cost_share_of_gross <= Decimal("0.15"),
        ),
        Criterion(
            "Fork simulation matches the model",
            "see fork report"
            if fork_within_tolerance is None
            else ("yes" if fork_within_tolerance else "no"),
            "within 0.3%",
            fork_within_tolerance,
        ),
        Criterion(
            "Max protocol exposure / max tier B exposure",
            f"{_pct(m.max_protocol_share, 1)} / {_pct(m.max_tier_b_share, 1)}",
            "<= 35% / <= 25%",
            m.max_protocol_share <= Decimal("0.35") + CAP_TOLERANCE
            and m.max_tier_b_share <= Decimal("0.25") + CAP_TOLERANCE,
        ),
        Criterion(
            "Yield positive after incident-catalog losses",
            f"{_pct(m.net_apy_after_incidents)} APY",
            "> 0",
            m.net_apy_after_incidents > 0,
        ),
    ]


def verdict(criteria: list[Criterion]) -> str:
    mandatory_failed = any(c.mandatory and c.passed is False for c in criteria)
    failed = [c for c in criteria if c.passed is False]
    unknown = [c for c in criteria if c.passed is None]
    if mandatory_failed:
        return "No-Go"
    if not failed and not unknown:
        return "Go"
    if len(failed) <= 1:
        return "Rework"
    return "No-Go"
