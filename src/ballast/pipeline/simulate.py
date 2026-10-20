"""Fork simulation of entry and exit for current risk-allowed candidates.

Entry (underlying -> PT) and exit (PT -> underlying) are quoted by Pendle with our test account
as receiver, pass the shared :class:`TxGuard`, and run on an Anvil fork. The PT received is
compared with the quote and with our cost model; the model must match within ``FORK_TOLERANCE_PCT``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select

from ballast.chains.client import JsonRpcChainClient, RpcError, decode_result, encode_call
from ballast.domain.models import Chain, TxRequest, utcnow
from ballast.domain.money import ONE, TokenAmount, pct
from ballast.execution.anvil import (
    ANVIL_TEST_ACCOUNT,
    AnvilForkExecutor,
    anvil_fork,
    erc20_balance,
    set_erc20_balance,
)
from ballast.execution.guards import TxGuard
from ballast.logging import get_logger
from ballast.pipeline.analyze import PENDLE_VENUE_FEE_BPS
from ballast.pipeline.context import AppContext
from ballast.sources.http import SourceUnavailableError
from ballast.sources.pendle import PENDLE_ROUTER_V4
from ballast.storage.db import session_scope
from ballast.storage.models import PendleMarketSnapshotRow, RiskScoreRow, ScanRow, SimulatedTxRow

log = get_logger(__name__)

SIM_SIZE_USD = Decimal(1000)
SIM_SLIPPAGE = Decimal("0.005")
BPS = Decimal(10_000)


@dataclass(frozen=True, slots=True)
class SimTarget:
    key: str
    chain: Chain
    underlying: str
    underlying_usd: Decimal
    pt: str
    pt_usd: Decimal
    fee_rate: Decimal | None
    days: Decimal


@dataclass(frozen=True, slots=True)
class SimOutcome:
    key: str
    leg: str
    success: bool
    deviation_vs_model: Decimal | None
    deviation_vs_quote: Decimal | None
    within_tolerance: bool | None
    gas_used: int
    error: str | None = None


async def _targets(ctx: AppContext) -> list[SimTarget]:
    now = utcnow()
    async with session_scope(ctx.sessions) as session:
        last_run = await session.scalar(select(func.max(ScanRow.started_at)))
        if last_run is None:
            return []
        scans = select(ScanRow.id).where(ScanRow.started_at == last_run)
        keys = set(
            (
                await session.scalars(
                    select(RiskScoreRow.opportunity_key).where(
                        RiskScoreRow.scan_id.in_(scans),
                        RiskScoreRow.allowed.is_(True),
                        RiskScoreRow.opportunity_key.like("pendle:%"),
                    )
                )
            ).all()
        )
        out: list[SimTarget] = []
        for key in sorted(keys):
            _, slug, market = key.split(":", 2)
            chain = Chain.from_slug(slug)
            snap = await session.scalar(
                select(PendleMarketSnapshotRow)
                .where(
                    PendleMarketSnapshotRow.chain_id == int(chain), PendleMarketSnapshotRow.market == market
                )
                .order_by(PendleMarketSnapshotRow.observed_at.desc())
                .limit(1)
            )
            if snap is None or not snap.underlying_usd or not snap.pt_usd:
                continue
            expiry = snap.expiry if snap.expiry.tzinfo else snap.expiry.replace(tzinfo=now.tzinfo)
            days = Decimal((expiry - now).total_seconds()) / Decimal(86400)
            out.append(
                SimTarget(
                    key,
                    chain,
                    snap.underlying,
                    snap.underlying_usd,
                    snap.pt,
                    snap.pt_usd,
                    snap.fee_rate,
                    days,
                )
            )
    return out


def _guard(ctx: AppContext, target: SimTarget, max_tx_usd: Decimal) -> TxGuard:
    """Router whitelist + approvals limited to whitelisted base tokens and this market's PT/underlying."""
    chain = target.chain
    tokens = {a.address_on(chain) for a in ctx.whitelist.assets} | {target.pt, target.underlying}
    return TxGuard(
        targets={chain: frozenset({PENDLE_ROUTER_V4})},
        tokens={chain: frozenset(t for t in tokens if t)},
        own_account=ANVIL_TEST_ACCOUNT,
        max_tx_usd=max_tx_usd,
    )


def model_pt_out(target: SimTarget, size_usd: Decimal, slippage_frac: Decimal) -> Decimal:
    """PT units our cost model expects for ``size_usd`` (gas is paid in ETH, not from the input)."""
    if target.fee_rate is not None and target.days > 0:
        fee_frac = target.fee_rate * target.days / Decimal(365)
    else:
        fee_frac = PENDLE_VENUE_FEE_BPS / BPS
    return size_usd * (ONE - fee_frac - slippage_frac) / target.pt_usd


async def simulate_target(
    ctx: AppContext, fork: JsonRpcChainClient, target: SimTarget, tolerance: Decimal
) -> list[SimOutcome]:
    executor = AnvilForkExecutor(fork, _guard(ctx, target, SIM_SIZE_USD * 2), native_usd=Decimal(3000))
    (decimals,) = decode_result(
        ["uint8"], await fork.call(target.underlying, encode_call("decimals()", [], []))
    )
    (pt_decimals,) = decode_result(["uint8"], await fork.call(target.pt, encode_call("decimals()", [], [])))
    amount_in = TokenAmount(SIM_SIZE_USD / target.underlying_usd, int(decimals)).to_raw(receive=False)
    if not await set_erc20_balance(fork, target.underlying, ANVIL_TEST_ACCOUNT, amount_in):
        return [
            SimOutcome(
                target.key, "entry", False, None, None, None, 0, "cannot fund test account (storage layout)"
            )
        ]

    entry = await ctx.pendle.quote(
        int(target.chain), target.underlying, amount_in, target.pt,
        receiver=ANVIL_TEST_ACCOUNT, slippage=SIM_SLIPPAGE, use_limit_order=False,
    )  # fmt: skip
    if entry is None:
        return [SimOutcome(target.key, "entry", False, None, None, None, 0, "no quote")]
    pt_before = await erc20_balance(fork, target.pt, ANVIL_TEST_ACCOUNT)
    result = await executor.execute(
        TxRequest(
            chain=target.chain,
            to=entry.tx_to,
            data=entry.tx_data,
            value_wei=0,
            value_usd=SIM_SIZE_USD,
            description=f"entry {target.key}",
            min_out_raw=int(Decimal(entry.amount_out_raw) * (ONE - SIM_SLIPPAGE)),
            approve_token=target.underlying,
            approve_amount_raw=amount_in,
            recipient=ANVIL_TEST_ACCOUNT,
        )
    )
    pt_out = await erc20_balance(fork, target.pt, ANVIL_TEST_ACCOUNT) - pt_before
    quoted_slippage = max(-entry.price_impact, Decimal(0))
    model = model_pt_out(target, SIM_SIZE_USD, quoted_slippage)
    actual = Decimal(pt_out).scaleb(-int(pt_decimals))
    vs_model = actual / model - 1 if result.success and model > 0 else None
    vs_quote = (
        Decimal(pt_out) / Decimal(entry.amount_out_raw) - 1
        if result.success and entry.amount_out_raw
        else None
    )
    within = None if vs_model is None else abs(vs_model) <= pct(tolerance)
    outcomes = [
        SimOutcome(
            target.key, "entry", result.success, vs_model, vs_quote, within, result.gas_used, result.error
        )
    ]
    await _store(
        ctx, fork, target=target, leg="entry", amount_in_raw=amount_in,
        expected_out=entry.amount_out_raw, actual_out=pt_out, outcome=outcomes[0],
    )  # fmt: skip
    if not result.success or pt_out <= 0:
        return outcomes

    exit_quote = await ctx.pendle.quote(
        int(target.chain), target.pt, pt_out, target.underlying,
        receiver=ANVIL_TEST_ACCOUNT, slippage=SIM_SLIPPAGE, use_limit_order=False,
    )  # fmt: skip
    if exit_quote is None:
        outcomes.append(SimOutcome(target.key, "exit", False, None, None, None, 0, "no exit quote"))
        return outcomes
    under_before = await erc20_balance(fork, target.underlying, ANVIL_TEST_ACCOUNT)
    exit_result = await executor.execute(
        TxRequest(
            chain=target.chain,
            to=exit_quote.tx_to,
            data=exit_quote.tx_data,
            value_wei=0,
            value_usd=SIM_SIZE_USD,
            description=f"exit {target.key}",
            min_out_raw=int(Decimal(exit_quote.amount_out_raw) * (ONE - SIM_SLIPPAGE)),
            approve_token=target.pt,
            approve_amount_raw=pt_out,
            recipient=ANVIL_TEST_ACCOUNT,
        )
    )
    under_out = await erc20_balance(fork, target.underlying, ANVIL_TEST_ACCOUNT) - under_before
    vs_quote_exit = (
        Decimal(under_out) / Decimal(exit_quote.amount_out_raw) - 1
        if exit_result.success and exit_quote.amount_out_raw
        else None
    )
    exit_outcome = SimOutcome(
        target.key,
        "exit",
        exit_result.success,
        None,
        vs_quote_exit,
        None if vs_quote_exit is None else abs(vs_quote_exit) <= pct(tolerance),
        exit_result.gas_used,
        exit_result.error,
    )
    outcomes.append(exit_outcome)
    await _store(
        ctx, fork, target=target, leg="exit", amount_in_raw=pt_out,
        expected_out=exit_quote.amount_out_raw, actual_out=under_out, outcome=exit_outcome,
    )  # fmt: skip
    return outcomes


async def _store(
    ctx: AppContext,
    fork: JsonRpcChainClient,
    *,
    target: SimTarget,
    leg: str,
    amount_in_raw: int,
    expected_out: int,
    actual_out: int,
    outcome: SimOutcome,
) -> None:
    block = await fork.block_number()
    async with session_scope(ctx.sessions) as session:
        session.add(
            SimulatedTxRow(
                created_at=utcnow(),
                opportunity_key=target.key,
                chain_id=int(target.chain),
                fork_block=block,
                description=f"{leg} amount_in_raw={amount_in_raw}",
                amount_in_usd=SIM_SIZE_USD,
                expected_out_raw=Decimal(expected_out),
                actual_out_raw=Decimal(actual_out) if outcome.success else None,
                deviation_pct=(outcome.deviation_vs_model if leg == "entry" else outcome.deviation_vs_quote),
                gas_used=outcome.gas_used,
                success=outcome.success,
                within_tolerance=outcome.within_tolerance,
                error=outcome.error,
            )
        )


async def run_simulations(ctx: AppContext) -> list[SimOutcome]:
    fork_settings = ctx.settings.fork
    chains_settings = ctx.settings.chains
    if fork_settings is None or chains_settings is None:
        msg = "fork/chain settings missing"
        raise RuntimeError(msg)
    targets = await _targets(ctx)
    outcomes: list[SimOutcome] = []
    for i, chain in enumerate(sorted({t.chain for t in targets}, key=int)):
        url = chains_settings.rpc_url(chain).get_secret_value()
        if not url:
            continue
        async with anvil_fork(
            url, chain=chain, port=fork_settings.anvil_port + i, binary=fork_settings.anvil_bin
        ) as fork:
            snapshot = await fork.rpc("evm_snapshot", [])
            for target in (t for t in targets if t.chain is chain):
                try:
                    outcomes += await simulate_target(ctx, fork, target, fork_settings.fork_tolerance_pct)
                except (RpcError, SourceUnavailableError, ValueError) as exc:
                    outcomes.append(
                        SimOutcome(target.key, "entry", False, None, None, None, 0, str(exc)[:300])
                    )
                await fork.rpc("evm_revert", [snapshot])
                snapshot = await fork.rpc("evm_snapshot", [])
    return outcomes
