"""Gas snapshots per chain, including the L1 data fee on rollups.

- Ethereum: ``eth_gasPrice``; no L1 fee.
- Base (OP Stack): ``GasPriceOracle.getL1FeeUpperBound(txSize)`` at 0x42...0F.
- Arbitrum: ``NodeInterface.gasEstimateL1Component`` at 0x...C8 for a payload of ``tx_size_bytes``;
  L1 fee = gasEstimateForL1 * baseFee.

A failed L1 estimate falls back to ``fallback_l1_fee_usd`` (conservative) and is logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ballast.chains.client import JsonRpcChainClient, RpcError, decode_result, encode_call
from ballast.costs.model import WEI_PER_NATIVE, GasSnapshot
from ballast.domain.models import Chain
from ballast.logging import get_logger

log = get_logger(__name__)

OP_GAS_PRICE_ORACLE = "0x420000000000000000000000000000000000000F"
ARB_NODE_INTERFACE = "0x00000000000000000000000000000000000000C8"
PENDLE_ROUTER = "0x888888888889758F76e7103c6CbF23ABbF58F946"


@dataclass(frozen=True, slots=True)
class GasCollector:
    tx_size_bytes: int = 1500  # typical Pendle router swap calldata incl. signature overhead
    fallback_l1_fee_usd: Decimal = Decimal("0.10")

    async def snapshot(self, client: JsonRpcChainClient, native_usd: Decimal) -> GasSnapshot:
        gas_price = await client.gas_price_wei()
        l1_wei: int | None = 0
        try:
            if client.chain is Chain.BASE:
                l1_wei = await self._op_l1_fee(client)
            elif client.chain is Chain.ARBITRUM:
                l1_wei = await self._arb_l1_fee(client)
        except (RpcError, ValueError) as exc:
            log.warning("l1_fee_estimate_failed", chain=client.chain.slug, error=str(exc))
            l1_wei = None
        l1_usd = self.fallback_l1_fee_usd if l1_wei is None else Decimal(l1_wei) * native_usd / WEI_PER_NATIVE
        return GasSnapshot(client.chain, gas_price, native_usd, l1_usd)

    async def _op_l1_fee(self, client: JsonRpcChainClient) -> int:
        data = encode_call("getL1FeeUpperBound(uint256)", ["uint256"], [self.tx_size_bytes])
        (fee,) = decode_result(["uint256"], await client.call(OP_GAS_PRICE_ORACLE, data))
        return int(fee)

    async def _arb_l1_fee(self, client: JsonRpcChainClient) -> int:
        payload = b"\x01" * self.tx_size_bytes  # non-zero bytes: worst case for calldata pricing
        data = encode_call(
            "gasEstimateL1Component(address,bool,bytes)",
            ["address", "bool", "bytes"],
            [PENDLE_ROUTER, False, payload],
        )
        gas_l1, base_fee, _l1_base = decode_result(
            ["uint64", "uint256", "uint256"], await client.call(ARB_NODE_INTERFACE, data)
        )
        return int(gas_l1) * int(base_fee)
