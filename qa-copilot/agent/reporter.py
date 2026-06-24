"""Generates structured bug reports and executive summary from raw exploration data."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from ai.client import stream_reason
from ai.prompts import final_report_prompt

logger = logging.getLogger(__name__)

SEVERITY_SCORE = {"critical": 40, "high": 20, "medium": 8, "low": 2}


def deduplicate_bugs(bugs: list[dict]) -> list[dict]:
    """Remove near-duplicate bugs by title similarity."""
    seen_titles: set[str] = set()
    unique: list[dict] = []
    for bug in bugs:
        key = bug["title"].lower()[:60]
        if key not in seen_titles:
            seen_titles.add(key)
            unique.append(bug)
    return unique


def score_session(bugs: list[dict]) -> dict[str, Any]:
    total = sum(SEVERITY_SCORE.get(b["severity"], 0) for b in bugs)
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for b in bugs:
        counts[b.get("severity", "low")] = counts.get(b.get("severity", "low"), 0) + 1

    if counts["critical"] > 0 or total > 80:
        health = "critical"
    elif counts["high"] > 2 or total > 40:
        health = "broken"
    elif counts["high"] > 0 or total > 15:
        health = "degraded"
    else:
        health = "healthy"

    return {"total_score": total, "by_severity": counts, "health": health}


async def generate_report(
    session_id: str,
    target_url: str,
    all_results: list[dict],
    persona_cross_report: dict,
    started_at: datetime,
    completed_at: datetime,
) -> dict[str, Any]:
    """Aggregate all persona results into a final structured report."""

    all_bugs: list[dict] = []
    total_pages = 0
    total_console_errors = 0
    total_network_failures = 0

    for result in all_results:
        all_bugs.extend(result.get("bug_candidates", []))
        total_pages += result.get("pages_visited", 0)
        total_console_errors += len(result.get("console_errors", []))
        total_network_failures += len(result.get("network_failures", []))

    unique_bugs = deduplicate_bugs(all_bugs)
    score = score_session(unique_bugs)
    duration_s = (completed_at - started_at).total_seconds()

    session_summary = {
        "session_id": session_id,
        "target_url": target_url,
        "personas_run": [r["persona"] for r in all_results],
        "total_bugs": len(unique_bugs),
        "severity_breakdown": score["by_severity"],
        "health": score["health"],
        "pages_visited": total_pages,
        "console_errors": total_console_errors,
        "network_failures": total_network_failures,
        "bug_titles": [b["title"] for b in unique_bugs[:20]],
        "persona_divergences": persona_cross_report.get("total_divergences", 0),
        "high_divergences": persona_cross_report.get("high_severity_divergences", 0),
    }

    ai_summary = await stream_reason(final_report_prompt(session_summary))

    return {
        "session_id": session_id,
        "target_url": target_url,
        "status": "completed",
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "duration_seconds": round(duration_s, 1),
        "score": score,
        "bugs": unique_bugs,
        "stats": {
            "total_bugs": len(unique_bugs),
            "pages_visited": total_pages,
            "console_errors": total_console_errors,
            "network_failures": total_network_failures,
        },
        "persona_analysis": persona_cross_report,
        "executive_summary": ai_summary.get("executive_summary", ""),
        "overall_health": ai_summary.get("overall_health", score["health"]),
        "critical_path_status": ai_summary.get("critical_path_status", "unknown"),
        "top_recommendations": ai_summary.get("top_recommendations", []),
        "test_coverage_assessment": ai_summary.get("test_coverage_assessment", ""),
    }
