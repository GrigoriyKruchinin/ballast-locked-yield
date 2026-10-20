"""Command line entry point: ``uv run ballast <command>``."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from ballast.chains.client import JsonRpcChainClient, RpcError, decode_result, encode_call
from ballast.config.settings import ChainSettings, Role, settings_for_role
from ballast.config.whitelist import load_whitelist
from ballast.logging import configure_logging
from ballast.pipeline.context import app_context
from ballast.pipeline.run import run_scan


async def _scan() -> int:
    settings = settings_for_role(Role.CLI)
    async with app_context(settings) as ctx:
        summary = await run_scan(ctx)
    print(f"markets={summary.n_markets} whitelisted={summary.n_whitelisted} errors={len(summary.errors)}")
    for err in summary.errors:
        print(f"  ! {err}")
    for book in summary.books:
        print(
            f"[{book.book}] value={book.total_value_usd:.2f} locked={book.locked_usd:.2f} "
            f"parked={book.parked_usd:.2f} idle={book.idle_usd:.2f} parking_apy={book.parking_apy:.2%} "
            f"eligible={len(book.eligible)} opened={len(book.opened)} matured={book.matured}"
        )
    return 0


async def _verify_whitelist() -> int:
    settings = settings_for_role(Role.CLI)
    whitelist = load_whitelist(settings.app.whitelist_path)
    chains = ChainSettings()
    failures = 0
    for asset in whitelist.assets:
        for chain, address in sorted(asset.addresses.items()):
            url = chains.rpc_url(chain).get_secret_value()
            if not url:
                print(f"SKIP {asset.symbol:6} {chain.slug:9} no RPC configured")
                continue
            async with JsonRpcChainClient(chain, url) as client:
                try:
                    (symbol,) = decode_result(
                        ["string"], await client.call(address, encode_call("symbol()", [], []))
                    )
                    (decimals,) = decode_result(
                        ["uint8"], await client.call(address, encode_call("decimals()", [], []))
                    )
                except (RpcError, ValueError) as exc:
                    print(f"FAIL {asset.symbol:6} {chain.slug:9} {address} {exc}")
                    failures += 1
                    continue
            names = {asset.symbol.lower(), *(a.lower() for a in asset.aliases)}
            normalized = str(symbol).replace("₮", "T").lower()
            ok = normalized in names
            failures += 0 if ok else 1
            status = "OK  " if ok else "FAIL"
            print(f"{status} {asset.symbol:6} {chain.slug:9} {address} symbol={symbol} decimals={decimals}")
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ballast", description="Ballast locked-yield research tool")
    parser.add_argument("--log-level", default="WARNING")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="collect, analyze and update paper portfolios once")
    sub.add_parser("verify-whitelist", help="check pinned token addresses on-chain (symbol, decimals)")
    args = parser.parse_args(argv)
    configure_logging(args.log_level)
    commands = {"scan": _scan, "verify-whitelist": _verify_whitelist}
    return asyncio.run(commands[args.command]())


if __name__ == "__main__":
    sys.exit(main())
