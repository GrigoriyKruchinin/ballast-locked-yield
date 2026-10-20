"""Anvil fork executor (implements :class:`ballast.domain.interfaces.ITxExecutor`).

Runs transactions from anvil's default unlocked test account on a local fork. That account and
its key are public test fixtures and never touch a real network.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from decimal import Decimal

from eth_abi.abi import encode
from eth_utils.crypto import keccak

from ballast.chains.client import JsonRpcChainClient, RpcError, decode_result, encode_call
from ballast.domain.models import Chain, TxRequest, TxResult
from ballast.execution.guards import GuardError, TxGuard
from ballast.logging import get_logger

log = get_logger(__name__)

ANVIL_TEST_ACCOUNT = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
# ERC-7201 namespace of OpenZeppelin ERC20Upgradeable v5 (balances mapping at offset 0).
OZ_ERC20_NAMESPACE = int("52c63247e1f47db19d5ce0460030c497f067ca4cebf71ba98eeadabe20bace00", 16)
MAX_SLOT_PROBE = 30


@contextlib.asynccontextmanager
async def anvil_fork(
    rpc_url: str, *, chain: Chain, port: int, binary: str = "anvil", block: int | None = None
) -> AsyncIterator[JsonRpcChainClient]:
    """Start ``anvil --fork-url`` and yield a client; the process is always terminated."""
    path = shutil.which(binary)
    if path is None:
        msg = f"{binary} not found (install Foundry)"
        raise FileNotFoundError(msg)
    args = [path, "--fork-url", rpc_url, "--port", str(port), "--silent", "--chain-id", str(int(chain))]
    if block is not None:
        args += ["--fork-block-number", str(block)]
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    client = JsonRpcChainClient(chain, f"http://127.0.0.1:{port}", timeout_seconds=120)
    try:
        async with client:
            for _ in range(120):
                if proc.returncode is not None:
                    msg = "anvil exited during startup (check RPC URL / archive access)"
                    raise RuntimeError(msg)
                try:
                    await client.block_number()
                    break
                except RpcError:
                    await asyncio.sleep(0.5)
            else:
                msg = "anvil did not start in time"
                raise RuntimeError(msg)
            yield client
    finally:
        if proc.returncode is None:
            proc.terminate()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=10)
            if proc.returncode is None:
                proc.kill()


def _word(value: int) -> str:
    return "0x" + value.to_bytes(32, "big").hex()


async def erc20_balance(client: JsonRpcChainClient, token: str, owner: str) -> int:
    (balance,) = decode_result(
        ["uint256"], await client.call(token, encode_call("balanceOf(address)", ["address"], [owner]))
    )
    return int(balance)


async def set_erc20_balance(client: JsonRpcChainClient, token: str, owner: str, amount: int) -> bool:
    """Write ``amount`` into the balances mapping by probing common storage layouts."""
    candidates: list[int] = []
    for slot in range(MAX_SLOT_PROBE):
        candidates.append(
            int.from_bytes(keccak(encode(["address", "uint256"], [owner, slot])), "big")
        )  # solidity
        candidates.append(
            int.from_bytes(keccak(encode(["uint256", "address"], [slot, owner])), "big")
        )  # vyper
    candidates.append(
        int.from_bytes(keccak(encode(["address", "uint256"], [owner, OZ_ERC20_NAMESPACE])), "big")
    )
    for storage_slot in candidates:
        await client.rpc("anvil_setStorageAt", [token, _word(storage_slot), _word(amount)])
        if await erc20_balance(client, token, owner) == amount:
            return True
        await client.rpc("anvil_setStorageAt", [token, _word(storage_slot), _word(0)])
    return False


@dataclass(slots=True)
class AnvilForkExecutor:
    client: JsonRpcChainClient
    guard: TxGuard
    native_usd: Decimal
    account: str = ANVIL_TEST_ACCOUNT
    sent: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "anvil-fork"

    async def _send(self, to: str, data: str, value_wei: int = 0) -> tuple[str, int, bool, int]:
        tx_hash = await self.client.rpc(
            "eth_sendTransaction",
            [{"from": self.account, "to": to, "data": data, "value": hex(value_wei), "gas": hex(5_000_000)}],
        )
        receipt = await self.client.rpc("eth_getTransactionReceipt", [tx_hash])
        if receipt is None:
            await self.client.rpc("evm_mine", [])
            receipt = await self.client.rpc("eth_getTransactionReceipt", [tx_hash])
        gas_used = int(receipt["gasUsed"], 16)
        price = int(receipt.get("effectiveGasPrice", "0x0"), 16)
        self.sent.append(str(tx_hash))
        return str(tx_hash), gas_used, receipt["status"] == "0x1", gas_used * price

    async def execute(self, request: TxRequest) -> TxResult:
        try:
            self.guard.check(request)
        except GuardError as exc:
            return TxResult(False, 0, Decimal(0), None, None, f"guard: {exc}")
        total_gas = 0
        total_wei = 0
        if request.approve_token is not None and request.approve_amount_raw is not None:
            data = encode_call(
                "approve(address,uint256)", ["address", "uint256"], [request.to, request.approve_amount_raw]
            )
            _, gas, ok, wei = await self._send(request.approve_token, data)
            total_gas, total_wei = total_gas + gas, total_wei + wei
            if not ok:
                return TxResult(False, total_gas, self._usd(total_wei), None, None, "approve reverted")
        tx_hash, gas, ok, wei = await self._send(request.to, request.data, request.value_wei)
        total_gas, total_wei = total_gas + gas, total_wei + wei
        return TxResult(
            ok, total_gas, self._usd(total_wei), None, tx_hash, None if ok else "transaction reverted"
        )

    def _usd(self, wei: int) -> Decimal:
        return Decimal(wei) * self.native_usd / Decimal(10**18)
