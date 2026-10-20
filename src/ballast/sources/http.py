"""Async JSON HTTP client with retries and header-driven rate limiting."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from ballast.logging import get_logger, redact_text

log = get_logger(__name__)

# Any: JSON payloads are untyped until validated by the caller's pydantic models.
Json = Any

RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
UNAVAILABLE_STATUS = frozenset({403, 451})


class SourceUnavailableError(RuntimeError):
    """Upstream refused us (geo-block, auth) or kept failing after retries."""


@dataclass(slots=True)
class RateLimitState:
    remaining: int | None = None
    reset_at: float | None = None


@dataclass(slots=True)
class JsonHttpClient:
    base_url: str
    timeout_seconds: float = 20.0
    max_retries: int = 3
    min_remaining: int = 10
    headers: Mapping[str, str] = field(default_factory=dict)
    transport: httpx.AsyncBaseTransport | None = None
    _client: httpx.AsyncClient | None = None
    _limit: RateLimitState = field(default_factory=RateLimitState)

    async def __aenter__(self) -> JsonHttpClient:
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout_seconds,
            headers={"user-agent": "ballast/0.1", **self.headers},
            transport=self.transport,
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get(self, path: str, params: Mapping[str, str | int] | None = None) -> Json:
        return await self._request("GET", path, params=params)

    async def post(self, path: str, body: Json) -> Json:
        return await self._request("POST", path, json=body)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        json: Json = None,
    ) -> Json:
        if self._client is None:
            msg = "client not started; use 'async with'"
            raise RuntimeError(msg)
        await self._respect_limit()
        last_error = ""
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.request(method, path, params=params, json=json)
            except httpx.TransportError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                self._update_limit(response)
                if response.status_code in UNAVAILABLE_STATUS:
                    msg = f"{self.base_url}{path}: HTTP {response.status_code}"
                    raise SourceUnavailableError(redact_text(msg))
                if response.status_code not in RETRY_STATUS:
                    response.raise_for_status()
                    return response.json()
                last_error = f"HTTP {response.status_code}"
            if attempt < self.max_retries:
                delay = min(2**attempt + random.random(), 30.0)  # noqa: S311 - jitter, not crypto
                log.warning("http_retry", url=self.base_url + path, error=last_error, delay=round(delay, 2))
                await asyncio.sleep(delay)
        msg = f"{self.base_url}{path}: {last_error} after {self.max_retries} retries"
        raise SourceUnavailableError(redact_text(msg))

    def _update_limit(self, response: httpx.Response) -> None:
        remaining = response.headers.get("x-ratelimit-remaining")
        reset = response.headers.get("x-ratelimit-reset")
        if remaining is not None and remaining.isdigit():
            self._limit.remaining = int(remaining)
        if reset is not None:
            try:
                self._limit.reset_at = float(reset)
            except ValueError:
                self._limit.reset_at = None

    async def _respect_limit(self) -> None:
        state = self._limit
        if state.remaining is None or state.remaining > self.min_remaining or state.reset_at is None:
            return
        wait = state.reset_at - time.time()
        if 0 < wait <= 120:
            log.info("rate_limit_wait", url=self.base_url, seconds=round(wait, 1))
            await asyncio.sleep(wait + 0.5)
        state.remaining = None
