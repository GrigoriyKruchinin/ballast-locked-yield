from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from ballast.config.settings import AllocationSettings, RiskSettings
from ballast.config.whitelist import load_whitelist
from ballast.domain.models import Chain, Opportunity, Tier
from ballast.nly.calculator import NlyCalculator
from ballast.risk.incidents import Incident, IncidentCatalog, IncidentKind, load_incidents
from ballast.risk.rules import DepegHistoryRule, MarketState, PegRule, TvlRule, YieldAnomalyRule
from ballast.risk.scorer import RiskScorer, default_rules, exposures_for
from tests.factories import NOW, ZERO_COSTS, FixedCostModel, parking, pt

WL = load_whitelist(Path(__file__).resolve().parents[2] / "config" / "whitelist.yaml")
SUSDE = "0x9D39A5DE30e57443BfF2A8307A4256c8797A3497"
USDC_ETH = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

CATALOG = IncidentCatalog(
    incidents=(
        Incident(
            id="usdc-svb-2023-03",
            date=date(2023, 3, 11),
            kind=IncidentKind.DEPEG,
            assets=("USDC",),
            protocols=("circle",),
            tier_like=Tier.A,
            min_price_usd=Decimal("0.87"),
            holder_loss_pct=Decimal(0),
            recovered=True,
            summary="SVB exposure",
            sources=("https://example.com/svb",),
        ),
        Incident(
            id="synthetic-collapse",
            date=date(2025, 11, 4),
            kind=IncidentKind.COLLAPSE,
            assets=("xUSD",),
            protocols=("stream",),
            tier_like=Tier.B,
            holder_loss_pct=Decimal(70),
            recovered=False,
            summary="collapse",
            sources=("https://example.com/x",),
        ),
    )
)


def state(**kw: object) -> MarketState:
    base: dict[str, object] = {
        "as_of": date(2026, 1, 1),
        "protocol_tvl_usd": {
            "ethena": Decimal(5_000_000_000),
            "aave-v3": Decimal(20_000_000_000),
            "sky": Decimal(5_000_000_000),
        },
        "asset_usd_price": {"usde": Decimal("0.999"), "usdc": Decimal(1), "usds": Decimal(1)},
        "parking_apy": Decimal("0.04"),
    }
    base.update(kw)
    return MarketState(**base)


def scorer(st: MarketState | None = None, *, execution: bool = False) -> RiskScorer:
    risk = RiskSettings()
    rules = default_rules(
        whitelist=WL,
        catalog=CATALOG,
        state=st or state(),
        risk=risk,
        allocation=AllocationSettings(),
        execution_mode=execution,
    )
    return RiskScorer(rules, WL, risk)


def susde() -> Opportunity:
    opp = pt(asset="sUSDe", protocol="ethena", chain=Chain.ETHEREUM, days=60)
    return replace(opp, asset_address=SUSDE, quoted_apy=Decimal("0.07"))


def test_whitelisted_tier_b_allowed() -> None:
    risk = scorer().score(susde())
    assert risk.tier is Tier.B
    assert risk.allowed, risk.reasons
    assert Decimal(10) < risk.score < Decimal(60)


def test_unknown_asset_blocked() -> None:
    opp = replace(pt(asset="USD3", protocol="ethena"), quoted_apy=Decimal("0.06"))
    risk = scorer().score(opp)
    assert not risk.allowed
    assert risk.tier is Tier.C


def test_address_mismatch_blocked() -> None:
    opp = replace(susde(), asset_address="0x000000000000000000000000000000000000dEaD")
    assert not scorer().score(opp).allowed


def test_unpinned_address_blocked_only_in_execution_mode() -> None:
    opp = replace(pt(asset="sUSDe", protocol="ethena", chain=Chain.BASE), quoted_apy=Decimal("0.06"))
    assert scorer().score(opp).allowed
    assert not scorer(execution=True).score(opp).allowed


def test_maturity_limits() -> None:
    assert not scorer().score(replace(susde(), maturity=NOW.replace(year=2027))).allowed
    short = pt(asset="sUSDe", protocol="ethena", chain=Chain.ETHEREUM, days=3)
    assert not scorer().score(replace(short, asset_address=SUSDE)).allowed


def test_depeg_blocks() -> None:
    st = state(asset_usd_price={"usde": Decimal("0.97")})
    risk = scorer(st).score(susde())
    assert not risk.allowed
    assert any("off peg" in r for r in risk.reasons)


def test_peg_warning_scores() -> None:
    rule = PegRule(state(asset_usd_price={"usde": Decimal("0.993")}), WL, Decimal("0.5"), Decimal(2))
    finding = rule.evaluate(susde())
    assert not finding.blocking
    assert finding.score > 0


def test_missing_peg_price_is_risk() -> None:
    rule = PegRule(state(asset_usd_price={}), WL, Decimal("0.5"), Decimal(2))
    assert rule.evaluate(susde()).score > 0


@pytest.mark.parametrize(
    ("tvl", "change", "blocking"),
    [
        (Decimal(1_000_000), Decimal(0), True),
        (Decimal(1_000_000_000), Decimal("-0.5"), True),
        (Decimal(1_000_000_000), Decimal("-0.25"), False),
    ],
)
def test_tvl_rule(tvl: Decimal, change: Decimal, blocking: bool) -> None:
    rule = TvlRule(
        state(protocol_tvl_usd={"ethena": tvl}, protocol_tvl_change_7d={"ethena": change}),
        Decimal(5_000_000),
        Decimal(20),
        Decimal(40),
    )
    finding = rule.evaluate(susde())
    assert finding.blocking is blocking
    assert finding.score > 0


def test_yield_anomaly() -> None:
    rule = YieldAnomalyRule(state())
    too_good = replace(susde(), quoted_apy=Decimal("0.25"))
    assert rule.evaluate(too_good).blocking
    elevated = replace(susde(), quoted_apy=Decimal("0.14"))
    finding = rule.evaluate(elevated)
    assert not finding.blocking
    assert finding.score == Decimal(10)


def test_threshold_includes_tier_and_score_premium() -> None:
    s = scorer()
    risk = s.score(susde())
    threshold = s.threshold_apy(risk, Decimal("0.04"))
    assert threshold == Decimal("0.04") + Decimal("0.06") + risk.score * Decimal("0.0002")


def test_exposures() -> None:
    exp = exposures_for(susde(), WL)
    assert exp == {"protocol:ethena", "venue:pendle", "asset:sUSDe", "tier:B", "chain:ethereum"}
    park = exposures_for(parking(), WL)
    assert "protocol:aave-v3" in park
    assert "protocol:circle" not in park  # unit-of-account issuer is cap-exempt
    assert not any(e.startswith("venue:") for e in park)


def test_candidate_build() -> None:
    opp = susde()
    nly = NlyCalculator(FixedCostModel(ZERO_COSTS)).evaluate(opp, Decimal(1000), NOW)
    cand = scorer().candidate(opp, nly, Decimal("0.04"))
    assert cand.excess_apy == cand.nly.nly_apy - cand.threshold_apy


def test_incident_base_rate() -> None:
    loss = CATALOG.annual_expected_loss(Tier.B, date(2024, 1, 1), date(2026, 1, 1), universe_size=10)
    assert abs(loss - Decimal("0.07") / (Decimal(731) / Decimal(365))) < Decimal("1e-20")
    assert CATALOG.annual_expected_loss(Tier.A, date(2024, 1, 1), date(2026, 1, 1), universe_size=8) == 0
    with pytest.raises(ValueError):
        CATALOG.annual_expected_loss(Tier.A, date(2024, 1, 1), date(2026, 1, 1), universe_size=0)


def test_project_catalog_loads_and_peg_history_applies() -> None:
    catalog = load_incidents(Path(__file__).resolve().parents[2] / "config" / "incidents.yaml")
    assert len(catalog.incidents) >= 20
    assert all(i.sources for i in catalog.incidents)
    finding = DepegHistoryRule(catalog, WL).evaluate(susde())
    assert "usde-binance-2025-10" in finding.reason  # peg-asset incidents count for sUSDe
