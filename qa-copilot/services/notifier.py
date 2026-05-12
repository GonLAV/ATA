"""Outbound notification sender (webhook / Slack / Discord) — n8n: alerting."""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from db.models import NotificationTarget

logger = logging.getLogger(__name__)

SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


async def dispatch_session_event(
    event_type: str,
    session_id: str,
    data: dict[str, Any],
    *,
    bug_severity: str | None = None,
) -> None:
    """Fired by the EventEmitter on session_completed / session_failed / bug_found."""
    from db.database import AsyncSessionLocal
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(NotificationTarget).where(NotificationTarget.active == True)  # noqa: E712
        )
        targets: list[NotificationTarget] = result.scalars().all()

    for target in targets:
        if event_type not in (target.events or []):
            continue
        if bug_severity and SEVERITY_RANK.get(bug_severity, 0) < SEVERITY_RANK.get(target.min_severity, 0):
            continue
        try:
            await _send(target, event_type, session_id, data)
        except Exception as exc:
            logger.warning("Notification to %s failed: %s", target.name, exc)


async def send_test_notification(target: NotificationTarget) -> tuple[bool, str]:
    """Sends a test ping. Returns (success, detail)."""
    try:
        await _send(target, "test", "test-session", {"message": "QA Copilot notification test"})
        return True, "Notification sent successfully"
    except Exception as exc:
        return False, str(exc)


async def _send(
    target: NotificationTarget, event_type: str, session_id: str, data: dict
) -> None:
    payload = _build_payload(target.target_type, event_type, session_id, data)

    if target.target_type in ("webhook", "slack", "discord"):
        if not target.url:
            raise ValueError("URL is required for webhook/Slack/Discord targets")
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(target.url, json=payload)
            resp.raise_for_status()

    elif target.target_type == "email":
        # Stub — integrate with an SMTP service or SendGrid as needed
        logger.info("Email notification to %s (stub): %s", target.email, json.dumps(payload)[:200])

    logger.info("Sent %s notification to %s (%s)", event_type, target.name, target.target_type)


def _build_payload(target_type: str, event_type: str, session_id: str, data: dict) -> dict:
    from config import settings

    base_url = getattr(settings, "public_url", "http://localhost:8000")
    report_url = f"{base_url}/?session={session_id}"
    health = data.get("health", "unknown").upper()
    bugs   = data.get("total_bugs", data.get("bugs_found", "?"))
    message = data.get("message", f"QA session {event_type}")

    if target_type == "slack":
        color = {"HEALTHY": "#36a64f", "DEGRADED": "#ff9800", "BROKEN": "#e53935"}.get(health, "#757575")
        return {
            "attachments": [{
                "color": color,
                "title": f"QA Copilot — {event_type.replace('_', ' ').title()}",
                "text": message,
                "fields": [
                    {"title": "Health",   "value": health,    "short": True},
                    {"title": "Bugs",     "value": str(bugs), "short": True},
                    {"title": "Session",  "value": session_id[:8], "short": True},
                    {"title": "Report",   "value": f"<{report_url}|View Report>", "short": False},
                ],
                "footer": "QA Copilot",
                "ts": __import__("time").time(),
            }]
        }

    if target_type == "discord":
        color_int = {"HEALTHY": 0x36A64F, "DEGRADED": 0xFF9800, "BROKEN": 0xE53935}.get(health, 0x757575)
        return {
            "embeds": [{
                "title": f"QA Copilot — {event_type.replace('_', ' ').title()}",
                "description": message,
                "color": color_int,
                "fields": [
                    {"name": "Health", "value": health, "inline": True},
                    {"name": "Bugs",   "value": str(bugs), "inline": True},
                ],
                "url": report_url,
            }]
        }

    # generic webhook / email stub
    return {
        "event":      event_type,
        "session_id": session_id,
        "message":    message,
        "data":       data,
        "report_url": report_url,
    }
