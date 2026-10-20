"""Command line entry point: ``uv run ballast <command>``."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from ballast.backtest.engine import BacktestAssumptions
from ballast.backtest.runner import render_report, run_backtests, write_report
from ballast.chains.client import JsonRpcChainClient, RpcError, decode_result, encode_call
from ballast.config.settings import ChainSettings, Role, settings_for_role
from ballast.config.whitelist import load_whitelist
from ballast.logging import configure_logging
from ballast.notify.service import send_digest
from ballast.notify.telegram import TelegramClient, TelegramError
from ballast.pipeline.context import app_context, books
from ballast.pipeline.run import run_scan
from ballast.storage.db import make_engine, make_sessionmaker


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


async def _notify_test() -> int:
    settings = settings_for_role(Role.NOTIFIER)
    tg = settings.telegram
    if tg is None or not tg.bot_token.get_secret_value() or not tg.chat_id:
        print("Telegram is not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
        return 1
    client = TelegramClient(tg.bot_token.get_secret_value(), tg.chat_id, tg.api_url)
    text = (
        "✅ <b>Ballast на связи</b>\n"
        "Это тестовое сообщение: бот настроен правильно.\n"
        "<i>Сюда будут приходить новые возможности, риск-события и итоги дня и недели.</i>"
    )
    try:
        await client.send(text)
    except TelegramError as exc:
        print(f"FAIL: {exc}")
        return 1
    print("sent")
    return 0


async def _digest(weekly: bool) -> int:
    settings = settings_for_role(Role.NOTIFIER)
    tg, db = settings.telegram, settings.db
    if tg is None or db is None:
        return 1
    engine = make_engine(db.url())
    try:
        client = TelegramClient(tg.bot_token.get_secret_value(), tg.chat_id, tg.api_url)
        names = [b.name for b in books(ChainSettings().chain_enabled)]
        sent = await send_digest(client, make_sessionmaker(engine), names, weekly=weekly)
    finally:
        await engine.dispose()
    print(f"sent {sent} message(s)")
    return 0


async def _backtest(output: str) -> int:
    settings = settings_for_role(Role.ANALYTICS)
    async with app_context(settings) as ctx:
        books_result, data, window = await run_backtests(ctx)
    text = render_report(books_result, data, window, BacktestAssumptions())
    path = write_report(text, Path(output))
    for b in books_result:
        m = b.report.validation_default.metrics
        print(
            f"[{b.book}] validation net APY {m.net_apy:.2%} "
            f"(after incidents {m.net_apy_after_incidents:.2%}), "
            f"parking {m.parking_apy_avg:.2%}, locked avg {m.avg_locked_share:.1%}, verdict {b.verdict}"
        )
    print(f"report: {path}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ballast", description="Ballast locked-yield research tool")
    parser.add_argument("--log-level", default="WARNING")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="collect, analyze and update paper portfolios once")
    sub.add_parser("verify-whitelist", help="check pinned token addresses on-chain (symbol, decimals)")
    sub.add_parser("notify-test", help="send a test Telegram message")
    backtest = sub.add_parser("backtest", help="out-of-sample backtest on stored Pendle history")
    backtest.add_argument("--output", default="docs/reports")
    digest = sub.add_parser("digest", help="send the daily (or --weekly) digest now")
    digest.add_argument("--weekly", action="store_true")
    args = parser.parse_args(argv)
    configure_logging(args.log_level)
    if args.command == "backtest":
        return asyncio.run(_backtest(args.output))
    if args.command == "digest":
        return asyncio.run(_digest(args.weekly))
    commands = {"scan": _scan, "verify-whitelist": _verify_whitelist, "notify-test": _notify_test}
    return asyncio.run(commands[args.command]())


if __name__ == "__main__":
    sys.exit(main())
