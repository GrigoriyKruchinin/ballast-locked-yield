"""Incident catalog: real depegs, collapses, hacks and freezes with sources."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from ballast.domain.models import Tier


class IncidentKind(StrEnum):
    DEPEG = "depeg"
    COLLAPSE = "collapse"
    HACK = "hack"
    FREEZE = "freeze"
    ORACLE = "oracle"
    GOVERNANCE = "governance"


class Incident(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9-]+$")
    date: date
    kind: IncidentKind
    assets: tuple[str, ...] = ()
    protocols: tuple[str, ...] = ()
    tier_like: Tier
    min_price_usd: Decimal | None = None
    duration_days: Decimal | None = None
    holder_loss_pct: Decimal = Decimal(0)
    recovered: bool
    summary: str
    sources: tuple[HttpUrl, ...] = Field(min_length=1)
    notes: str = ""


class IncidentCatalog(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    incidents: tuple[Incident, ...]

    def for_asset(self, symbol: str) -> list[Incident]:
        wanted = symbol.lower()
        return [i for i in self.incidents if wanted in {a.lower() for a in i.assets}]

    def for_protocol(self, slug: str) -> list[Incident]:
        return [i for i in self.incidents if slug in i.protocols]

    def by_tier(self, tier: Tier) -> list[Incident]:
        return [i for i in self.incidents if i.tier_like is tier]

    def annual_expected_loss(self, tier: Tier, start: date, end: date) -> Decimal:
        """Sum of holder losses (fraction) per year for ``tier``-like incidents in ``[start, end]``.

        Assumes an equal-weight holder exposed to one affected asset at a time; a crude base rate
        that is reported alongside the sample size, never as a precise probability.
        """
        years = Decimal((end - start).days) / Decimal(365)
        if years <= 0:
            msg = "empty window"
            raise ValueError(msg)
        losses = sum(
            (i.holder_loss_pct / Decimal(100) for i in self.by_tier(tier) if start <= i.date <= end),
            Decimal(0),
        )
        return losses / years


def load_incidents(path: Path) -> IncidentCatalog:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return IncidentCatalog.model_validate(raw)
