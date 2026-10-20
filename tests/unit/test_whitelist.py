from pathlib import Path

import pytest
from pydantic import ValidationError

from ballast.config.whitelist import Whitelist, load_whitelist
from ballast.domain.models import Chain, Tier

WHITELIST = Path(__file__).resolve().parents[2] / "config" / "whitelist.yaml"


def test_project_whitelist_loads() -> None:
    wl = load_whitelist(WHITELIST)
    assert {a.tier for a in wl.assets} <= {Tier.A, Tier.B}
    assert wl.find_asset("usdc", Chain.ARBITRUM) is not None
    assert wl.find_asset("aArbUSDCn", Chain.ARBITRUM) is not None  # alias


def test_unknown_asset_rejected() -> None:
    wl = load_whitelist(WHITELIST)
    assert wl.find_asset("USD0++", Chain.ARBITRUM) is None
    assert wl.find_asset("syrupUSDC", Chain.BASE) is None


def test_pinned_address_must_match() -> None:
    wl = load_whitelist(WHITELIST)
    real = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
    fake = "0x0000000000000000000000000000000000000001"
    usdc = wl.find_asset("USDC", Chain.ARBITRUM, real.lower())
    assert usdc is not None
    assert wl.is_address_verified(usdc, Chain.ARBITRUM, real)
    assert wl.find_asset("USDC", Chain.ARBITRUM, fake) is None


def test_unpinned_asset_is_not_verified() -> None:
    wl = load_whitelist(WHITELIST)
    usde = wl.find_asset("USDe", Chain.ARBITRUM, "0x5d3a1Ff2b6BAb83b63cd9AD0787074081a52ef34")
    assert usde is not None
    assert not wl.is_address_verified(usde, Chain.ARBITRUM, "0x5d3a1Ff2b6BAb83b63cd9AD0787074081a52ef34")


def _minimal(tier: str, protocol: str = "p") -> dict[str, object]:
    return {
        "protocols": {
            "p": {
                "kind": "issuer",
                "launched": "2020-01",
                "audits": 1,
                "admin_pause": False,
                "upgradeable": False,
                "centralized_counterparty": False,
                "oracle_dependency": "none",
            }
        },
        "assets": [{"symbol": "X", "tier": tier, "protocols": [protocol], "par_redemption": True}],
    }


def test_tier_c_cannot_be_whitelisted() -> None:
    with pytest.raises(ValidationError, match="tier C"):
        Whitelist.model_validate(_minimal("C"))


def test_unknown_protocol_reference() -> None:
    with pytest.raises(ValidationError, match="unknown protocols"):
        Whitelist.model_validate(_minimal("A", protocol="nope"))
