from dataclasses import replace
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from ballast.allocation.allocator import GreedyAllocator
from ballast.config.settings import AllocationSettings
from ballast.domain.models import (
    Allocation,
    Candidate,
    Chain,
    DecisionAction,
    NlyResult,
    Opportunity,
    RiskScore,
    SourceKind,
    Tier,
)
from tests.factories import NOW, ZERO_COSTS, parking, pt

CAP = Decimal(10_000)
MIN = {Chain.ETHEREUM: Decimal(500), Chain.ARBITRUM: Decimal(100), Chain.BASE: Decimal(100)}


def nly(key: str, apy: str) -> NlyResult:
    a = Decimal(apy)
    return NlyResult(key, CAP, Decimal(90), CAP, CAP, ZERO_COSTS, a, a, a, a / 12, a)


def cand(
    opp: Opportunity,
    apy: str,
    *,
    threshold: str = "0.06",
    tier: Tier = Tier.A,
    allowed: bool = True,
    exposures: set[str] | None = None,
) -> Candidate:
    exp = exposures or {
        f"protocol:{opp.protocol}",
        "venue:pendle",
        f"asset:{opp.asset_symbol}",
        f"tier:{tier}",
    }
    return Candidate(
        opportunity=opp,
        nly=nly(opp.key, apy),
        risk=RiskScore(tier=tier, score=Decimal(10), findings=(), allowed=allowed),
        threshold_apy=Decimal(threshold),
        exposures=frozenset(exp),
    )


def park(key: str, protocol: str, apy: str) -> Candidate:
    return cand(
        parking(key=key, protocol=protocol, apy=apy), apy, threshold="0", exposures={f"protocol:{protocol}"}
    )


def allocator(**kw: object) -> GreedyAllocator:
    return GreedyAllocator(settings=AllocationSettings(), min_position_usd=MIN, **kw)  # type: ignore[arg-type]


def big_pt(key: str, protocol: str, asset: str = "USDC") -> Opportunity:
    return pt(key=key, protocol=protocol, asset=asset, liquidity="100000000")


def test_protocol_cap_35pct() -> None:
    plan = allocator().allocate([cand(big_pt("a", "sky"), "0.10")], [], CAP, NOW)
    assert plan.locked_usd == Decimal(3500)


def test_ranked_by_excess_and_venue_cap() -> None:
    cands = [
        cand(big_pt("a", "sky"), "0.08"),
        cand(big_pt("b", "aave-v3"), "0.10"),
        cand(big_pt("c", "morpho"), "0.09"),
    ]
    plan = allocator().allocate(cands, [], CAP, NOW)
    sizes = {a.opportunity_key: a.capital_usd for a in plan.allocations}
    assert sizes == {"b": Decimal(3500), "c": Decimal(3500), "a": Decimal(0)} or sizes == {
        "b": Decimal(3500),
        "c": Decimal(3500),
    }
    # venue cap 70% binds: nothing left for "a"
    skipped = {d.opportunity_key: d.reason for d in plan.decisions if d.action is DecisionAction.SKIP}
    assert "venue:pendle" in skipped["a"]


def test_tier_b_caps() -> None:
    cands = [
        cand(big_pt("x", "ethena", "sUSDe"), "0.15", tier=Tier.B),
        cand(big_pt("y", "ethena", "USDe"), "0.14", tier=Tier.B),
        cand(big_pt("z", "other", "fooUSD"), "0.13", tier=Tier.B),
    ]
    plan = allocator().allocate(cands, [], CAP, NOW)
    sizes = {a.opportunity_key: a.capital_usd for a in plan.allocations}
    assert sizes["x"] == Decimal(1500)  # per tier-B asset 15%
    assert sizes["y"] == Decimal(1000)  # tier B total 25%
    assert "z" not in sizes


def test_reserve_kept_out_of_locked() -> None:
    cands = [cand(big_pt(k, f"p{k}"), "0.10") for k in "abcdef"]
    s = AllocationSettings(max_venue_pct=Decimal(100))
    plan = GreedyAllocator(settings=s, min_position_usd=MIN).allocate(cands, [], CAP, NOW)
    assert plan.locked_usd == Decimal(9500)


def test_below_threshold_and_risk_skipped() -> None:
    plan = allocator().allocate(
        [cand(big_pt("lo", "sky"), "0.05"), cand(big_pt("bad", "aave-v3"), "0.20", allowed=False)],
        [],
        CAP,
        NOW,
    )
    assert plan.locked_usd == 0
    reasons = {d.opportunity_key: d.reason for d in plan.decisions}
    assert "below threshold" in reasons["lo"]
    assert reasons["bad"].startswith("risk")


def test_liquidity_cap_and_min_position() -> None:
    # 2% of 20k liquidity = 400 < 500 minimum on Ethereum
    opp = replace(pt(key="thin", protocol="sky", liquidity="20000"), chain=Chain.ETHEREUM)
    plan = allocator().allocate([cand(opp, "0.10")], [], CAP, NOW)
    assert plan.locked_usd == 0
    assert "market liquidity" in plan.decisions[0].reason


def test_parking_respects_caps_and_idle() -> None:
    plan = allocator().allocate(
        [], [park("aave", "aave-v3", "0.05"), park("comp", "compound-v3", "0.04")], CAP, NOW
    )
    assert plan.parked_usd == Decimal(7000)
    assert plan.idle_usd == Decimal(3000)


def test_parking_shares_protocol_cap_with_locked() -> None:
    plan = allocator().allocate(
        [cand(big_pt("pt-a", "aave-v3", "aUSDC"), "0.10")], [park("aave", "aave-v3", "0.05")], CAP, NOW
    )
    assert plan.locked_usd == Decimal(3500)
    assert plan.parked_usd == 0  # aave cap used by the PT
    assert plan.idle_usd == Decimal(6500)


def test_existing_positions_count() -> None:
    existing = [
        Allocation(
            "old",
            Decimal(3000),
            SourceKind.PENDLE_PT,
            frozenset({"protocol:sky", "venue:pendle"}),
            Decimal("0.1"),
            None,
        )
    ]
    plan = allocator(existing=existing).allocate([cand(big_pt("new", "sky"), "0.10")], [], CAP, NOW)
    assert plan.allocations[0].capital_usd == Decimal(500)


def test_resize_rechecks_threshold() -> None:
    def worse(c: Candidate, size: Decimal) -> Candidate:
        return replace(c, nly=nly(c.opportunity.key, "0.01"))

    plan = allocator(resize=worse).allocate([cand(big_pt("a", "sky"), "0.10")], [], CAP, NOW)
    assert plan.locked_usd == 0
    assert "at size" in plan.decisions[0].reason


protocols = st.sampled_from(["sky", "aave-v3", "ethena", "morpho", "spark", "euler"])


@settings(max_examples=200)
@given(
    specs=st.lists(
        st.tuples(protocols, st.booleans(), st.decimals(min_value="0.06", max_value="0.3", places=3)),
        max_size=12,
    ),
    capital=st.decimals(min_value=1000, max_value=100_000, places=0),
)
def test_invariants(specs: list[tuple[str, bool, Decimal]], capital: Decimal) -> None:
    cands = []
    for i, (proto, tier_b, apy) in enumerate(specs):
        tier = Tier.B if tier_b else Tier.A
        cands.append(cand(big_pt(f"k{i}", proto, f"asset{i % 3}"), str(apy), tier=tier))
    plan = allocator().allocate(
        cands, [park("aave", "aave-v3", "0.04"), park("comp", "compound-v3", "0.03")], capital, NOW
    )
    total = plan.locked_usd + plan.parked_usd + plan.idle_usd
    assert total == capital
    assert plan.locked_usd <= capital * Decimal("0.95")
    used: dict[str, Decimal] = {}
    for a in plan.allocations:
        for e in a.exposures:
            used[e] = used.get(e, Decimal(0)) + a.capital_usd
    for key, amount in used.items():
        if key.startswith("protocol:"):
            assert amount <= capital * Decimal("0.35")
        if key == "tier:B":
            assert amount <= capital * Decimal("0.25")
        if key.startswith("venue:"):
            assert amount <= capital * Decimal("0.70")
    assert all(a.capital_usd >= 0 for a in plan.allocations)
