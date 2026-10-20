"""Celery application, queues and beat schedule.

Queues map to containers (see docker-compose.local.yml):
- ``scan``: collector worker (RPC + DB): full scan every ``SNAPSHOT_INTERVAL_MINUTES``;
- ``analytics``: analytics worker: history backfill, quotes, backtests, reports;
- ``notify``: telegram worker (DB + bot token only): alerts and digests.
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from ballast.config.settings import DataSourceSettings, RedisSettings

redis_url = RedisSettings().redis_url
interval_minutes = DataSourceSettings().snapshot_interval_minutes

app = Celery("ballast", broker=redis_url, backend=redis_url, include=["ballast.workers.tasks"])
app.conf.update(
    task_default_queue="scan",
    task_routes={
        "ballast.scan": {"queue": "scan"},
        "ballast.notify_events": {"queue": "notify"},
        "ballast.daily_digest": {"queue": "notify"},
        "ballast.weekly_digest": {"queue": "notify"},
        "ballast.backfill_history": {"queue": "analytics"},
        "ballast.refresh_quotes": {"queue": "analytics"},
    },
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_time_limit=15 * 60,
    task_soft_time_limit=12 * 60,
    result_expires=24 * 3600,
    timezone="UTC",
    beat_schedule={
        "scan": {"task": "ballast.scan", "schedule": interval_minutes * 60.0},
        "refresh-quotes": {"task": "ballast.refresh_quotes", "schedule": crontab(minute=7)},
        "daily-digest": {"task": "ballast.daily_digest", "schedule": crontab(hour=6, minute=5)},
        "weekly-digest": {
            "task": "ballast.weekly_digest",
            "schedule": crontab(hour=6, minute=10, day_of_week=1),
        },
        "backfill-history": {"task": "ballast.backfill_history", "schedule": crontab(hour=3, minute=0)},
    },
)
