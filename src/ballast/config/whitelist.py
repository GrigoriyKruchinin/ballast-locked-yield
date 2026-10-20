"""Asset/protocol whitelist: the single source of what Ballast may touch."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ballast.domain.models import Chain, Tier


class OracleDependency(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ProtocolInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["venue", "lending", "issuer"]
    launched: str = Field(pattern=r"^\d{4}-\d{2}$")
    audits: int = Field(ge=0)
    admin_pause: bool
    upgradeable: bool
    centralized_counterparty: bool
    oracle_dependency: OracleDependency
    notes: str = ""


class AssetInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    tier: Tier
    protocols: tuple[str, ...]
    par_redemption: bool
    yield_bearing: bool = False
    aliases: tuple[str, ...] = ()
    addresses: dict[Chain, str] = Field(default_factory=dict)

    @field_validator("addresses", mode="before")
    @classmethod
    def _parse_chains(cls, value: object) -> object:
        if isinstance(value, dict):
            return {Chain.from_slug(str(k)): str(v) for k, v in value.items()}
        return value

    @model_validator(mode="after")
    def _no_tier_c(self) -> AssetInfo:
        if self.tier is Tier.C:
            msg = f"{self.symbol}: tier C assets cannot be whitelisted"
            raise ValueError(msg)
        return self

    def address_on(self, chain: Chain) -> str | None:
        return self.addresses.get(chain)


class ParkingInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    protocol: str
    asset: str
    chains: tuple[Chain, ...]

    @field_validator("chains", mode="before")
    @classmethod
    def _parse_chains(cls, value: object) -> object:
        if isinstance(value, list | tuple):
            return tuple(Chain.from_slug(str(v)) for v in value)
        return value


class Whitelist(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    protocols: dict[str, ProtocolInfo]
    assets: tuple[AssetInfo, ...]
    parking: tuple[ParkingInfo, ...] = ()

    @model_validator(mode="after")
    def _references_exist(self) -> Whitelist:
        for asset in self.assets:
            missing = [p for p in asset.protocols if p not in self.protocols]
            if missing:
                msg = f"{asset.symbol}: unknown protocols {missing}"
                raise ValueError(msg)
        symbols = {a.symbol for a in self.assets}
        for park in self.parking:
            if park.protocol not in self.protocols:
                msg = f"parking: unknown protocol {park.protocol}"
                raise ValueError(msg)
            if park.asset not in symbols:
                msg = f"parking: unknown asset {park.asset}"
                raise ValueError(msg)
        return self

    def find_asset(self, symbol: str, chain: Chain, address: str | None = None) -> AssetInfo | None:
        """Look up an asset. If the whitelist pins an address for ``chain``, it must match."""
        wanted = symbol.strip().lower()
        for asset in self.assets:
            names = {asset.symbol.lower(), *(a.lower() for a in asset.aliases)}
            if wanted not in names:
                continue
            pinned = asset.address_on(chain)
            if pinned is not None and address is not None and pinned.lower() != address.lower():
                return None
            return asset
        return None

    def is_address_verified(self, asset: AssetInfo, chain: Chain, address: str | None) -> bool:
        pinned = asset.address_on(chain)
        return pinned is not None and address is not None and pinned.lower() == address.lower()


def load_whitelist(path: Path) -> Whitelist:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Whitelist.model_validate(raw)
