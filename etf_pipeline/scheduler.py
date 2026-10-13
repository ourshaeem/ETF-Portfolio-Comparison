"""Scheduled extraction: run on start-up, then every weekday at 22:30 UTC
(6:30pm New York in summer, 5:30pm in winter — after the 4pm close either way)."""
from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from . import pipeline

log = logging.getLogger(__name__)


def _job() -> None:
    try:
        pipeline.run()
    except Exception:  # noqa: BLE001 - keep the scheduler alive; next run retries
        log.exception("Scheduled pipeline run failed")


def start() -> None:
    _job()
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(_job, CronTrigger(day_of_week="mon-fri", hour=22, minute=30), id="daily_pipeline")
    log.info("Scheduler started — next run: weekdays 22:30 UTC")
    scheduler.start()
