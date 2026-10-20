"""Minimal async JSON-RPC chain client (implements :class:`ballast.domain.interfaces.IChainClient`).

Read-only: block number, gas price, ``eth_call``, fee history. ABI encoding uses ``eth_abi``.
RPC URLs contain API keys, so they are never logged (errors are redacted).
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
from eth_abi.abi import decode, encode
from eth_utils.crypto import keccak

from ballast.domain.models import Chain
from ballast.logging import redact_text


class RpcError(RuntimeError):
    pass


def selector(signature: str) -> bytes:
    return keccak(text=signature)[:4]


def encode_call(signature: str, arg_types: Sequence[str], args: Sequence[object]) -> str:
    return "0x" + (selector(signature) + encode(list(arg_types), list(args))).hex()


def decode_result(types: Sequence[str], data: bytes) -> tuple[Any, ...]:  # Any: ABI values are heterogeneous
    return tuple(decode(list(types), data))


@dataclass(slots=True)
class JsonRpcChainClient:
    chain_id: Chain
    url: str
    timeout_seconds: float = 20.0
    transport: httpx.AsyncBaseTransport | None = None
    _ids: itertools.count[int] = field(default_factory=lambda: itertools.count(1))
    _client: httpx.AsyncClient | None = None

    @property
    def chain(self) -> Chain:
        return self.chain_id

    async def __aenter__(self) -> JsonRpcChainClient:
        self._client = httpx.AsyncClient(timeout=self.timeout_seconds, transport=self.transport)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def rpc(self, method: str, params: list[object]) -> Any:  # noqa: ANN401 - raw JSON-RPC result
        if self._client is None:
            msg = "client not started; use 'async with'"
            raise RuntimeError(msg)
        payload = {"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params}
        try:
            response = await self._client.post(self.url, json=payload)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RpcError(redact_text(f"{self.chain.slug} {method}: {exc}")) from None
        if "error" in body:
            raise RpcError(redact_text(f"{self.chain.slug} {method}: {body['error']}"))
        return body.get("result")

    async def block_number(self) -> int:
        return int(await self.rpc("eth_blockNumber", []), 16)

    async def gas_price_wei(self) -> int:
        return int(await self.rpc("eth_gasPrice", []), 16)

    async def call(self, to: str, data: str, block: int | None = None) -> bytes:
        tag = hex(block) if block is not None else "latest"
        result = await self.rpc("eth_call", [{"to": to, "data": data}, tag])
        return bytes.fromhex(str(result).removeprefix("0x"))

    async def base_fee_history(self, blocks: int = 20) -> list[int]:
        result = await self.rpc("eth_feeHistory", [hex(blocks), "latest", []])
        return [int(x, 16) for x in result.get("baseFeePerGas", [])]
