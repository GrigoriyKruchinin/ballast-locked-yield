"""Celery tasks. Each task builds only the settings of its role and runs one asyncio loop."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from ballast.config.settings import ChainSettings, Role, TelegramSettings, settings_for_role
from ballast.logging import configure_logging, get_logger
from ballast.notify.service import notify_events, send_digest
from ballast.notify.telegram import TelegramClient
from ballast.pipeline.context import app_context, books
from ballast.pipeline.history import backfill_history
from ballast.pipeline.quotes import refresh_quotes
from ballast.pipeline.run import run_scan
from ballast.pipeline.simulate import run_simulations
from ballast.storage.db import make_engine, make_sessionmaker
from ballast.workers.celery_app import app

log = get_logger(__name__)


def _run[T](coro_factory: Callable[[], Awaitable[T]]) -> T:
    async def runner() -> T:
        return await coro_factory()

    return asyncio.run(runner())


@app.task(name="ballast.scan")
def scan() -> dict[str, str]:
    settings = settings_for_role(Role.COLLECTOR)
    configure_logging(settings.app.log_level, json=True)

    async def go() -> dict[str, str]:
        async with app_context(settings) as ctx:
            summary = await run_scan(ctx)
        return {
            "markets": str(summary.n_markets),
            "errors": str(len(summary.errors)),
            "events": str(len(summary.events)),
            **{f"value_{b.book}": f"{b.total_value_usd:.2f}" for b in summary.books},
        }

    result = _run(go)
    notify.delay()
    return result


def _telegram(settings: TelegramSettings) -> TelegramClient:
    token = settings.bot_token.get_secret_value() if settings.enabled else ""
    return TelegramClient(token, settings.chat_id, settings.api_url)


@app.task(name="ballast.notify_events")
def notify() -> int:
    settings = settings_for_role(Role.NOTIFIER)
    if settings.db is None or settings.telegram is None:
        return 0
    db, tg = settings.db, settings.telegram

    async def go() -> int:
        engine = make_engine(db.url())
        try:
            return await notify_events(_telegram(tg), make_sessionmaker(engine))
        finally:
            await engine.dispose()

    return _run(go)


def _digest(*, weekly: bool) -> int:
    settings = settings_for_role(Role.NOTIFIER)
    if settings.db is None or settings.telegram is None:
        return 0
    db, tg = settings.db, settings.telegram
    names = [b.name for b in books(ChainSettings().chain_enabled)]

    async def go() -> int:
        engine = make_engine(db.url())
        try:
            return await send_digest(_telegram(tg), make_sessionmaker(engine), names, weekly=weekly)
        finally:
            await engine.dispose()

    return _run(go)


@app.task(name="ballast.daily_digest")
def daily_digest() -> int:
    return _digest(weekly=False)


@app.task(name="ballast.weekly_digest")
def weekly_digest() -> int:
    return _digest(weekly=True)


@app.task(name="ballast.backfill_history", time_limit=3 * 3600, soft_time_limit=170 * 60)
def backfill(max_markets: int | None = 80) -> dict[str, int]:
    settings = settings_for_role(Role.ANALYTICS)

    async def go() -> dict[str, int]:
        async with app_context(settings) as ctx:
            report = await backfill_history(
                ctx.pendle, ctx.sessions, list(ctx.chain_clients), max_markets=max_markets
            )
        return {
            "seen": report.markets_seen,
            "loaded": report.markets_loaded,
            "points": report.points_added,
            "failures": report.failures,
        }

    return _run(go)


@app.task(name="ballast.refresh_quotes")
def quotes() -> int:
    settings = settings_for_role(Role.ANALYTICS)

    async def go() -> int:
        async with app_context(settings) as ctx:
            return await refresh_quotes(ctx)

    return _run(go)


@app.task(name="ballast.simulate", time_limit=60 * 60, soft_time_limit=55 * 60)
def simulate() -> dict[str, int]:
    settings = settings_for_role(Role.SIMULATOR)

    async def go() -> dict[str, int]:
        async with app_context(settings) as ctx:
            outcomes = await run_simulations(ctx)
        return {
            "runs": len(outcomes),
            "ok": sum(1 for o in outcomes if o.success and o.within_tolerance is not False),
        }

    return _run(go)
