"""Helpers to parse untyped upstream JSON into Decimal / datetime safely."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation


def dec(value: object) -> Decimal | None:
    """Parse a JSON number/string into Decimal. Floats go through ``repr`` (shortest round-trip form)."""
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    text = repr(value) if isinstance(value, float) else str(value)
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def dec_or_zero(value: object) -> Decimal:
    return dec(value) or Decimal(0)


def parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def from_unix(value: object, *, millis: bool = False) -> datetime | None:
    number = dec(value)
    if number is None:
        return None
    seconds = number / 1000 if millis else number
    return datetime.fromtimestamp(float(seconds), tz=UTC)


def split_asset_id(asset_id: object) -> tuple[int, str] | None:
    """Pendle asset ids look like ``"42161-0xabc..."``."""
    if not isinstance(asset_id, str) or "-" not in asset_id:
        return None
    chain, _, address = asset_id.partition("-")
    if not chain.isdigit() or not address.startswith("0x"):
        return None
    return int(chain), address


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
