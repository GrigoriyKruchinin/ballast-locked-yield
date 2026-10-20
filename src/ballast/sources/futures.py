"""Dated futures vs spot basis (data only, no trading).

Basis APR = (F / S - 1) * 365 / days_to_expiry, with S from the same venue's index.
Each venue is optional: a geo-block (HTTP 451/403) or outage only removes that venue.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from ballast.domain.money import DAYS_PER_YEAR
from ballast.logging import get_logger
from ballast.sources.http import JsonHttpClient, SourceUnavailableError
from ballast.sources.parsing import dec, from_unix

log = get_logger(__name__)

MIN_DAYS = Decimal(3)
UNDERLYINGS = ("BTC", "ETH")


@dataclass(frozen=True, slots=True)
class BasisSnapshot:
    venue: str
    instrument: str
    underlying: str
    expiry: datetime
    future_price: Decimal
    index_price: Decimal
    observed_at: datetime

    def days_to_expiry(self) -> Decimal:
        return Decimal((self.expiry - self.observed_at).total_seconds()) / Decimal(86400)

    @property
    def basis_apr(self) -> Decimal | None:
        days = self.days_to_expiry()
        if days < MIN_DAYS or self.index_price <= 0:
            return None
        return (self.future_price / self.index_price - 1) * DAYS_PER_YEAR / days


class IFuturesVenue(Protocol):
    @property
    def name(self) -> str: ...

    async def snapshots(self, now: datetime) -> list[BasisSnapshot]: ...


@dataclass(slots=True)
class Deribit:
    http: JsonHttpClient

    @property
    def name(self) -> str:
        return "deribit"

    async def snapshots(self, now: datetime) -> list[BasisSnapshot]:
        out: list[BasisSnapshot] = []
        for coin in UNDERLYINGS:
            index_body = await self.http.get("/public/get_index_price", {"index_name": f"{coin.lower()}_usd"})
            index = (
                dec(index_body.get("result", {}).get("index_price")) if isinstance(index_body, dict) else None
            )
            instruments = await self.http.get("/public/get_instruments", {"currency": coin, "kind": "future"})
            expiries: dict[str, datetime] = {}
            for row in instruments.get("result", []) if isinstance(instruments, dict) else []:
                name = str(row.get("instrument_name", ""))
                expiry = from_unix(row.get("expiration_timestamp"), millis=True)
                if "PERPETUAL" not in name and expiry is not None:
                    expiries[name] = expiry
            book = await self.http.get(
                "/public/get_book_summary_by_currency", {"currency": coin, "kind": "future"}
            )
            for row in book.get("result", []) if isinstance(book, dict) else []:
                name = str(row.get("instrument_name", ""))
                mark = dec(row.get("mark_price"))
                if name in expiries and mark is not None and index is not None:
                    out.append(BasisSnapshot(self.name, name, coin, expiries[name], mark, index, now))
        return out


@dataclass(slots=True)
class Okx:
    http: JsonHttpClient

    @property
    def name(self) -> str:
        return "okx"

    async def snapshots(self, now: datetime) -> list[BasisSnapshot]:
        out: list[BasisSnapshot] = []
        for coin in UNDERLYINGS:
            family = f"{coin}-USD"
            inst = await self.http.get(
                "/api/v5/public/instruments", {"instType": "FUTURES", "instFamily": family}
            )
            expiries = {
                str(r.get("instId")): exp
                for r in (inst.get("data", []) if isinstance(inst, dict) else [])
                if (exp := from_unix(r.get("expTime"), millis=True)) is not None and r.get("state") == "live"
            }
            marks = await self.http.get(
                "/api/v5/public/mark-price", {"instType": "FUTURES", "instFamily": family}
            )
            idx = await self.http.get("/api/v5/market/index-tickers", {"instId": family})
            idx_rows = idx.get("data", []) if isinstance(idx, dict) else []
            index = dec(idx_rows[0].get("idxPx")) if idx_rows else None
            for row in marks.get("data", []) if isinstance(marks, dict) else []:
                inst_id = str(row.get("instId"))
                mark = dec(row.get("markPx"))
                if inst_id in expiries and mark is not None and index is not None:
                    out.append(BasisSnapshot(self.name, inst_id, coin, expiries[inst_id], mark, index, now))
        return out


@dataclass(slots=True)
class BinanceUsdm:
    http: JsonHttpClient

    @property
    def name(self) -> str:
        return "binance"

    async def snapshots(self, now: datetime) -> list[BasisSnapshot]:
        info = await self.http.get("/fapi/v1/exchangeInfo")
        out: list[BasisSnapshot] = []
        for row in info.get("symbols", []) if isinstance(info, dict) else []:
            if (
                row.get("contractType") not in {"CURRENT_QUARTER", "NEXT_QUARTER"}
                or row.get("status") != "TRADING"
            ):
                continue
            coin = str(row.get("baseAsset", ""))
            expiry = from_unix(row.get("deliveryDate"), millis=True)
            if coin not in UNDERLYINGS or expiry is None:
                continue
            symbol = str(row.get("symbol"))
            prem = await self.http.get("/fapi/v1/premiumIndex", {"symbol": symbol})
            if not isinstance(prem, dict):
                continue
            mark, index = dec(prem.get("markPrice")), dec(prem.get("indexPrice"))
            if mark is not None and index is not None:
                out.append(BasisSnapshot(self.name, symbol, coin, expiry, mark, index, now))
        return out


async def collect_basis(venues: Sequence[IFuturesVenue], now: datetime) -> list[BasisSnapshot]:
    out: list[BasisSnapshot] = []
    for venue in venues:
        try:
            out.extend(await venue.snapshots(now))
        except SourceUnavailableError as exc:
            log.warning("futures_venue_unavailable", venue=venue.name, error=str(exc))
    return out
