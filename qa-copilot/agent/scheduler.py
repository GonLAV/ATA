"""APScheduler-based cron runner for scheduled QA sessions (n8n: scheduled triggers)."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_scheduler = None   # lazy-init in start_scheduler()


def get_scheduler():
    global _scheduler
    if _scheduler is None:
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            _scheduler = AsyncIOScheduler(timezone="UTC")
        except ImportError:
            logger.warning("APScheduler not installed — scheduled sessions disabled")
    return _scheduler


async def start_scheduler() -> None:
    """Called once during app startup to restore all enabled schedules."""
    sched = get_scheduler()
    if sched is None:
        return

    from db.database import AsyncSessionLocal
    from db.models import Schedule
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Schedule).where(Schedule.enabled == True))  # noqa: E712
        schedules = result.scalars().all()

    if not sched.running:
        sched.start()
        logger.info("APScheduler started")

    for s in schedules:
        try:
            _add_job(sched, s)
        except Exception as exc:
            logger.warning("Could not schedule %s: %s", s.id, exc)

    logger.info("Restored %d scheduled QA jobs", len(schedules))


def stop_scheduler() -> None:
    sched = get_scheduler()
    if sched and sched.running:
        sched.shutdown(wait=False)
        logger.info("APScheduler stopped")


def register_schedule(schedule) -> None:
    """Add or replace a cron job for a Schedule model instance."""
    sched = get_scheduler()
    if sched is None:
        return
    if not sched.running:
        sched.start()
    _add_job(sched, schedule)


def unregister_schedule(schedule_id: str) -> None:
    sched = get_scheduler()
    if sched is None:
        return
    try:
        sched.remove_job(schedule_id)
        logger.info("Removed cron job for schedule %s", schedule_id)
    except Exception:
        pass


async def trigger_schedule_now(schedule) -> str:
    """Immediately run a scheduled session and return the session_id."""
    session_id = await _launch_session(schedule.id, schedule.url, schedule.config)
    return session_id


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _add_job(sched, schedule) -> None:
    parts = schedule.cron_expr.strip().split()
    if len(parts) == 5:
        minute, hour, day, month, day_of_week = parts
        second = "0"
    else:
        second, minute, hour, day, month, day_of_week = parts

    job_kwargs: dict[str, Any] = {
        "func": _run_scheduled_session,
        "trigger": "cron",
        "id": schedule.id,
        "replace_existing": True,
        "kwargs": {"schedule_id": schedule.id, "url": schedule.url, "config": schedule.config},
        "second":       second,
        "minute":       minute,
        "hour":         hour,
        "day":          day,
        "month":        month,
        "day_of_week":  day_of_week,
    }
    sched.add_job(**job_kwargs)
    logger.info("Registered cron '%s' for schedule %s (%s)", schedule.cron_expr, schedule.id, schedule.name)


async def _run_scheduled_session(schedule_id: str, url: str, config: dict) -> None:
    logger.info("Cron firing for schedule %s → %s", schedule_id, url)
    try:
        session_id = await _launch_session(schedule_id, url, config)
        await _update_last_run(schedule_id, session_id)
    except Exception as exc:
        logger.error("Scheduled session failed for %s: %s", schedule_id, exc, exc_info=True)


async def _launch_session(schedule_id: str, url: str, config: dict) -> str:
    from db.database import AsyncSessionLocal
    from db.models import Session as SessionModel, SessionStatus
    from agent.coordinator import SessionCoordinator

    async with AsyncSessionLocal() as db:
        session = SessionModel(url=url, config=config, status=SessionStatus.pending)
        db.add(session)
        await db.commit()
        await db.refresh(session)
        session_id = session.id
        coordinator = SessionCoordinator(db)
        await coordinator.run_session(session)

    return session_id


async def _update_last_run(schedule_id: str, session_id: str) -> None:
    from db.database import AsyncSessionLocal
    from db.models import Schedule
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
        s = result.scalar_one_or_none()
        if s:
            s.last_run_at = datetime.now(timezone.utc).replace(tzinfo=None)
            s.last_session_id = session_id
            await db.commit()
