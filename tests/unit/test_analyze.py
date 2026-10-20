from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

from ballast.config.settings import AllocationSettings, CostSettings, RiskSettings
from ballast.config.whitelist import load_whitelist
from ballast.costs.model import GasSnapshot
from ballast.domain.models import Chain, DecisionAction
from ballast.pipeline.analyze import AnalysisInputs, Analyzer, reference_parking_apy
from ballast.risk.incidents import load_incidents
from ballast.risk.rules import MarketState
from tests.factories import NOW, parking, pt

ROOT = Path(__file__).resolve().parents[2]
WL = load_whitelist(ROOT / "config" / "whitelist.yaml")
CAT = load_incidents(ROOT / "config" / "incidents.yaml")
SUSDS = "0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD"
GAS = {
    Chain.ETHEREUM: GasSnapshot(Chain.ETHEREUM, 1_000_000_000, Decimal(2500)),
    Chain.ARBITRUM: GasSnapshot(Chain.ARBITRUM, 20_000_000, Decimal(2500), Decimal("0.001")),
}
STATE = MarketState(
    as_of=date(2026, 1, 1),
    protocol_tvl_usd={"sky": Decimal(5_000_000_000), "aave-v3": Decimal(16_000_000_000)},
    asset_usd_price={"usds": Decimal(1), "usdc": Decimal(1)},
)


def analyzer() -> Analyzer:
    return Analyzer(WL, CAT, RiskSettings(), AllocationSettings(), CostSettings())


def susds_pt(price: str, days: int, chain: Chain = Chain.ETHEREUM):  # type: ignore[no-untyped-def]
    opp = pt(
        key=f"pendle:{chain.slug}:{price}:{days}",
        asset="sUSDS",
        protocol="sky",
        chain=chain,
        price=price,
        days=days,
    )
    return replace(opp, asset_address=SUSDS, quoted_apy=Decimal("0.09"), liquidity_usd=Decimal(5_000_000))


def inputs(*opps):  # type: ignore[no-untyped-def]
    park = replace(
        parking(apy="0.04"), chain=Chain.ETHEREUM, asset_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
    )
    return AnalysisInputs(list(opps), [park], GAS, STATE)


def test_reference_parking_is_best_rate() -> None:
    assert reference_parking_apy([parking(apy="0.03"), parking(apy="0.05")]) == Decimal("0.05")
    assert reference_parking_apy([]) == 0


def test_long_attractive_pt_enters() -> None:
    # 0.96 for 150 days ~ 10% APY gross; threshold = 4% + 2% + score premium
    res = analyzer().run(inputs(susds_pt("0.96", 150)), capital_usd=Decimal(10_000), now=NOW)
    enters = [d for d in res.plan.decisions if d.action is DecisionAction.ENTER]
    assert len(enters) == 1
    assert enters[0].capital_usd == Decimal(3500)  # sky protocol cap
    assert res.min_positions[Chain.ETHEREUM] > Decimal(500)  # 800k gas at 1 gwei, 2500 USD/ETH


def test_short_pt_skipped_by_costs() -> None:
    res = analyzer().run(inputs(susds_pt("0.995", 36)), capital_usd=Decimal(10_000), now=NOW)
    assert res.plan.locked_usd == 0
    assert not res.eligible


def test_chain_filter_and_missing_gas() -> None:
    base_pt = susds_pt("0.96", 150, chain=Chain.BASE)  # no gas snapshot for Base
    res = analyzer().run(
        inputs(base_pt, susds_pt("0.96", 150)), capital_usd=Decimal(10_000), now=NOW, chains=[Chain.ARBITRUM]
    )
    assert res.candidates == ()
    assert res.parking == ()


def test_matured_reported_as_error() -> None:
    res = analyzer().run(inputs(susds_pt("0.99", 0)), capital_usd=Decimal(10_000), now=NOW)
    assert res.errors and "matured" in res.errors[0].reason
