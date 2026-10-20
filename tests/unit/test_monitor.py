from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from ballast.config.settings import RiskSettings
from ballast.config.whitelist import load_whitelist
from ballast.domain.models import Chain, MonitorEvent, Severity
from ballast.monitor.monitors import MonitorInputs, RiskMonitor, throttle
from ballast.sources.defillama import ProtocolTvl
from tests.factories import NOW, parking, pt

WL = load_whitelist(Path(__file__).resolve().parents[2] / "config" / "whitelist.yaml")
PEGS = {"usdc": Decimal(1), "usdt": Decimal(1), "dai": Decimal(1), "usds": Decimal(1), "usde": Decimal(1)}


def inputs(**kw: object) -> MonitorInputs:
    base: dict[str, object] = {
        "observed_at": NOW,
        "pegs": PEGS,
        "tvl": {},
        "opportunities": [],
        "previous_apy": {},
        "parking": [],
        "previous_parking_apy": {},
        "errors": [],
    }
    base.update(kw)
    return MonitorInputs(**base)  # type: ignore[arg-type]


def mon() -> RiskMonitor:
    return RiskMonitor(WL, RiskSettings())


def test_quiet_when_all_normal() -> None:
    assert mon().evaluate(inputs()) == []


def test_depeg_levels_and_missing() -> None:
    pegs = {**PEGS, "usde": Decimal("0.993"), "usdt": Decimal("0.97")}
    del pegs["dai"]
    events = {(e.kind, e.subject, e.severity) for e in mon().evaluate(inputs(pegs=pegs))}
    assert ("depeg", "USDE", Severity.WARNING) in events
    assert ("depeg", "USDT", Severity.CRITICAL) in events
    assert ("peg_missing", "DAI", Severity.WARNING) in events


def test_tvl_drop() -> None:
    tvl = {
        "ethena": ProtocolTvl("ethena-usde", Decimal(55), Decimal(100), NOW),
        "sky": ProtocolTvl("sky-lending", Decimal(75), Decimal(100), NOW),
        "aave-v3": ProtocolTvl("aave-v3", Decimal(99), None, NOW),
    }
    events = {(e.subject, e.severity) for e in mon().evaluate(inputs(tvl=tvl))}
    assert events == {("ethena", Severity.CRITICAL), ("sky", Severity.WARNING)}


def test_rate_spike_only_for_whitelisted() -> None:
    wl_opp = replace(
        pt(key="a", asset="sUSDe", protocol="ethena", chain=Chain.ETHEREUM), quoted_apy=Decimal("0.20")
    )
    other = replace(pt(key="b", asset="USD3"), quoted_apy=Decimal("0.40"))
    events = mon().evaluate(
        inputs(opportunities=[wl_opp, other], previous_apy={"a": Decimal("0.05"), "b": Decimal("0.05")})
    )
    assert [e.subject for e in events] == ["a"]
    assert events[0].value == Decimal(15)


def test_parking_move_and_source_error() -> None:
    p = parking(key="pk", apy="0.10")
    events = mon().evaluate(
        inputs(parking=[p], previous_parking_apy={"pk": Decimal("0.04")}, errors=["pendle 1: timeout"])
    )
    assert {e.kind for e in events} == {"parking_rate_move", "source_error"}


def test_throttle() -> None:
    e = MonitorEvent("depeg", Severity.WARNING, "USDE", "x", None, NOW)
    recent = [("depeg", "USDE", "warning", NOW - timedelta(hours=1))]
    assert throttle([e], recent) == []
    assert throttle([e], [("depeg", "USDE", "warning", NOW - timedelta(hours=7))]) == [e]
    escalated = replace(e, severity=Severity.CRITICAL)
    assert throttle([escalated], recent) == [escalated]
