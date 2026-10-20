import json
from decimal import Decimal

import httpx
import pytest
from eth_abi.abi import encode

from ballast.chains.client import JsonRpcChainClient, RpcError, encode_call, selector
from ballast.chains.gas import GasCollector
from ballast.domain.models import Chain

URL = "https://arb-mainnet.g.alchemy.com/v2/SUPERSECRETKEY123456"


def rpc_transport(eth_call_result: bytes | None, gas_price: int = 10**8) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body["method"]
        if method == "eth_gasPrice":
            result: object = hex(gas_price)
        elif method == "eth_blockNumber":
            result = hex(123)
        elif method == "eth_call":
            if eth_call_result is None:
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": body["id"], "error": {"message": "revert"}}
                )
            result = "0x" + eth_call_result.hex()
        else:
            return httpx.Response(500)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    return httpx.MockTransport(handler)


def test_selector_known_value() -> None:
    assert selector("transfer(address,uint256)").hex() == "a9059cbb"
    assert encode_call("decimals()", [], []) == "0x313ce567"


async def test_basic_calls() -> None:
    async with JsonRpcChainClient(Chain.ETHEREUM, URL, transport=rpc_transport(b"")) as c:
        assert await c.block_number() == 123
        assert await c.gas_price_wei() == 10**8


async def test_ethereum_has_no_l1_fee() -> None:
    async with JsonRpcChainClient(Chain.ETHEREUM, URL, transport=rpc_transport(b"")) as c:
        snap = await GasCollector().snapshot(c, Decimal(3000))
    assert snap.l1_fee_usd_per_tx == 0
    # 1e6 gas * 1e8 wei = 1e14 wei = 1e-4 ETH = 0.3 USD
    assert snap.tx_cost_usd(1_000_000) == Decimal("0.3")


async def test_base_l1_fee() -> None:
    fee = encode(["uint256"], [10**12])  # 1e-6 ETH
    async with JsonRpcChainClient(Chain.BASE, URL, transport=rpc_transport(fee)) as c:
        snap = await GasCollector().snapshot(c, Decimal(3000))
    assert snap.l1_fee_usd_per_tx == Decimal("0.003")


async def test_arbitrum_l1_fee() -> None:
    out = encode(["uint64", "uint256", "uint256"], [50_000, 10**7, 10**9])
    async with JsonRpcChainClient(Chain.ARBITRUM, URL, transport=rpc_transport(out)) as c:
        snap = await GasCollector().snapshot(c, Decimal(3000))
    assert snap.l1_fee_usd_per_tx == Decimal("0.0015")  # 5e4 * 1e7 = 5e11 wei


async def test_l1_failure_uses_conservative_fallback() -> None:
    async with JsonRpcChainClient(Chain.BASE, URL, transport=rpc_transport(None)) as c:
        snap = await GasCollector(fallback_l1_fee_usd=Decimal("0.1")).snapshot(c, Decimal(3000))
    assert snap.l1_fee_usd_per_tx == Decimal("0.1")


async def test_rpc_errors_do_not_leak_key() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot connect to {request.url}")

    async with JsonRpcChainClient(Chain.ARBITRUM, URL, transport=httpx.MockTransport(boom)) as c:
        with pytest.raises(RpcError) as info:
            await c.block_number()
    assert "SUPERSECRETKEY123456" not in str(info.value)
