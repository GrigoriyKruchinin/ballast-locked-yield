import warnings
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import exc as sa_exc

from ballast.domain.models import Allocation, AllocationPlan, Chain, CostEstimate, Opportunity, SourceKind
from ballast.nly.calculator import NlyCalculator
from ballast.portfolio.paper import PaperEngine, PaperState
from ballast.portfolio.store import closed_positions, load_state, save_daily, save_state
from ballast.storage.db import make_engine, make_sessionmaker, session_scope
from ballast.storage.models import Base
from tests.factories import NOW, FixedCostModel, pt

COSTS = CostEstimate(Decimal(2), Decimal(1), Decimal(3), Decimal(1), Decimal(0))
CALC = NlyCalculator(FixedCostModel(COSTS))
PARK_KEY = "parking:aave-v3:arbitrum:USDC:aa"
PARK2 = "parking:sky:ethereum:USDS:bb"


def engine() -> PaperEngine:
    return PaperEngine(
        evaluate=lambda o, s: CALC.evaluate(o, s, NOW), parking_exit_cost=lambda c: Decimal("0.5")
    )


def plan(*allocs: Allocation) -> AllocationPlan:
    return AllocationPlan(allocations=allocs, decisions=(), idle_usd=Decimal(0))


def park_alloc(key: str, amount: str, apy: str = "0.04") -> Allocation:
    return Allocation(key, Decimal(amount), SourceKind.PARKING, frozenset({"protocol:x"}), Decimal(apy), None)


def pt_alloc(opp: Opportunity, amount: str) -> Allocation:
    return Allocation(
        opp.key, Decimal(amount), opp.kind, frozenset({"protocol:sky"}), Decimal("0.1"), opp.maturity
    )


def test_parking_deposit_and_accrual() -> None:
    state = PaperState.new("all", Decimal(10_000), NOW)
    eng = engine()
    _, moves, costs = eng.apply_plan(state, plan(park_alloc(PARK_KEY, "9500")), {}, NOW)
    assert moves == 1
    assert costs == Decimal("0.5")
    assert state.parking[PARK_KEY].balance_usd == Decimal("9499.5")
    assert state.idle_usd == Decimal(500)
    later = NOW + timedelta(days=365)
    accrued, closed, _ = eng.accrue_and_mature(state, later, {PARK_KEY: Decimal("0.04")}, {}, str)
    assert accrued == Decimal("379.98")
    assert not closed


def test_open_and_mature_position() -> None:
    state = PaperState.new("all", Decimal(10_000), NOW)
    eng = engine()
    opp = pt(price="0.98", days=73)
    opened, _, costs = eng.apply_plan(state, plan(pt_alloc(opp, "3500")), {opp.key: opp}, NOW)
    assert len(opened) == 1
    pos = opened[0]
    assert pos.capital_usd == Decimal(3500)
    assert costs == Decimal(6)  # gas entry 2 + fees 3 + slippage entry 1
    assert abs(pos.units - (Decimal(3500) - 6) / Decimal("0.98")) < Decimal("1e-20")
    # value drops by exactly the entry costs
    assert state.total_value(NOW) == Decimal(10_000) - 6
    at_maturity = NOW + timedelta(days=73)
    assert abs(state.total_value(at_maturity) - (Decimal(6500) + pos.expected_payoff_usd)) < Decimal("1e-20")
    _, closed, _ = eng.accrue_and_mature(state, at_maturity, {}, {"usdc": Decimal("0.999")}, str)
    assert len(closed) == 1
    expected = pos.units * Decimal("0.999") - 1  # peg haircut, exit gas 1
    assert abs(closed[0].payoff_usd - expected) < Decimal("1e-20")
    assert state.locked == []


def test_duplicate_entry_ignored_and_funding_from_parking() -> None:
    state = PaperState.new("all", Decimal(10_000), NOW)
    eng = engine()
    eng.apply_plan(state, plan(park_alloc(PARK_KEY, "10000")), {}, NOW)
    opp = pt(price="0.98", days=73)
    eng.apply_plan(state, plan(pt_alloc(opp, "3000"), park_alloc(PARK_KEY, "6500")), {opp.key: opp}, NOW)
    eng.apply_plan(state, plan(pt_alloc(opp, "3000"), park_alloc(PARK_KEY, "6500")), {opp.key: opp}, NOW)
    assert len(state.locked) == 1
    assert state.locked[0].capital_usd == Decimal(3000)


def test_missing_parking_rate_withdraws_to_idle() -> None:
    state = PaperState.new("all", Decimal(1000), NOW)
    eng = engine()
    eng.apply_plan(state, plan(park_alloc(PARK_KEY, "1000")), {}, NOW)
    eng.accrue_and_mature(state, NOW + timedelta(hours=1), {}, {}, str)
    assert state.parking == {}
    assert state.idle_usd == Decimal(999)  # 1000 - 0.5 entry - 0.5 exit


@settings(max_examples=150)
@given(
    steps=st.lists(
        st.tuples(
            st.decimals(min_value=0, max_value=6000, places=0),
            st.decimals(min_value=0, max_value=6000, places=0),
            st.decimals(min_value=0, max_value=4000, places=0),
        ),
        min_size=1,
        max_size=6,
    )
)
def test_value_conservation(steps: list[tuple[Decimal, Decimal, Decimal]]) -> None:
    """Total value only changes by costs paid when applying plans (no accrual between steps)."""
    state = PaperState.new("all", Decimal(10_000), NOW)
    eng = engine()
    for i, (p1, p2, locked) in enumerate(steps):
        opp = replace(pt(price="0.98", days=90), key=f"pt{i}")
        allocs = [park_alloc(PARK_KEY, str(p1)), park_alloc(PARK2, str(p2), "0.05")]
        if locked > 0:
            allocs.append(pt_alloc(opp, str(locked)))
        before = state.total_value(NOW)
        _, _, costs = eng.apply_plan(state, plan(*allocs), {opp.key: opp}, NOW)
        after = state.total_value(NOW)
        assert abs(before - after - costs) < Decimal("1e-20")
        assert state.idle_usd >= Decimal("-1e-20")
        assert all(b.balance_usd > 0 for b in state.parking.values())


async def test_state_roundtrip() -> None:
    warnings.filterwarnings("ignore", category=sa_exc.SAWarning)
    db = make_engine("sqlite+aiosqlite:///:memory:")
    async with db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = make_sessionmaker(db)
    state = PaperState.new("all", Decimal(10_000), NOW)
    eng = engine()
    opp = pt(price="0.98", days=10)
    eng.apply_plan(state, plan(pt_alloc(opp, "3000"), park_alloc(PARK_KEY, "7000")), {opp.key: opp}, NOW)
    async with session_scope(factory) as s:
        assert await load_state(s, "all") is None
        await save_state(s, state, [Chain.ARBITRUM])
    async with session_scope(factory) as s:
        loaded = await load_state(s, "all")
        assert loaded is not None
        assert len(loaded.locked) == 1
        assert set(loaded.parking) == {PARK_KEY}
        assert abs(loaded.total_value(NOW) - state.total_value(NOW)) < Decimal("1e-9")
        eng.accrue_and_mature(loaded, NOW + timedelta(days=11), {PARK_KEY: Decimal("0.04")}, {}, str)
        await save_state(s, loaded, [Chain.ARBITRUM])
        await save_daily(s, loaded, (NOW + timedelta(days=11)).date(), Decimal(1), Decimal("0.04"))
    async with session_scope(factory) as s:
        again = await load_state(s, "all")
        assert again is not None
        assert again.locked == []
        assert len(await closed_positions(s, "all")) == 1
    await db.dispose()
