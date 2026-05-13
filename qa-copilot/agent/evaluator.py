"""
Evaluation Engine — multi-dimensional accuracy scoring with AI-powered feedback.

Scoring overview
────────────────
Every completed QA session is evaluated across 8 dimensions (each 0-100).
A weighted average produces the overall accuracy percentage.

Dimension          Weight  What it measures
─────────────────  ──────  ──────────────────────────────────────────────────
coverage             15%   Pages explored vs estimated app size
bug_quality          20%   Per-bug report quality (title, steps, description)
precision            20%   Estimated true-positive rate (anti-false-positive)
detail               15%   Completeness of expected/actual behavior fields
severity_cal         10%   Severity calibration accuracy
ai_confidence        10%   Average AI confidence across bug candidates
monitoring           05%   Console errors + network failures captured
persona_div          05%   Bug diversity across the three personas

Ground-truth mode (optional)
─────────────────────────────
If the caller supplies `expected_bugs` (a known list of bug titles), the engine
also computes Precision, Recall, and F1 by fuzzy-matching against found bugs.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    Bug, ConsoleError, NetworkFailure, PageNode, Session,
    TestRun, Evaluation, EvaluationFeedback, EvaluationStatus,
)

logger = logging.getLogger(__name__)

# ── Dimension weights (must sum to 1.0) ──────────────────────────────────────
WEIGHTS = {
    "coverage":    0.15,
    "bug_quality": 0.20,
    "precision":   0.20,
    "detail":      0.15,
    "severity":    0.10,
    "confidence":  0.10,
    "monitoring":  0.05,
    "persona_div": 0.05,
}

# ── Grade thresholds ──────────────────────────────────────────────────────────
def _grade(score: float) -> str:
    if score >= 90: return "A"
    if score >= 80: return "B"
    if score >= 70: return "C"
    if score >= 60: return "D"
    return "F"


@dataclass
class BugScore:
    bug_id: str
    title: str
    score: float          # 0-100
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


@dataclass
class DimensionResult:
    name: str
    score: float          # 0-100
    weight: float
    feedback_items: list[dict] = field(default_factory=list)  # {title, detail, suggestion, severity, priority}


@dataclass
class EvaluationResult:
    session_id: str
    overall_score: float
    accuracy_pct: float
    grade: str
    dimensions: dict[str, DimensionResult]
    bug_scores: list[BugScore]
    feedback: list[dict]
    ai_summary: str
    # Optional ground-truth fields
    gt_recall_pct: float | None = None
    gt_precision_pct: float | None = None
    gt_f1_score: float | None = None
    gt_found_bugs: int | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

async def run_evaluation(
    session_id: str,
    db: AsyncSession,
    *,
    expected_bugs: list[str] | None = None,
    use_ai_summary: bool = True,
) -> EvaluationResult:
    """
    Run all evaluation dimensions for `session_id`.
    Persists an `Evaluation` + `EvaluationFeedback` rows into the DB.
    Returns the full `EvaluationResult`.
    """
    # ── Load session data ────────────────────────────────────────────────────
    sess_res = await db.execute(select(Session).where(Session.id == session_id))
    session: Session = sess_res.scalar_one_or_none()
    if not session:
        raise ValueError(f"Session {session_id} not found")

    bugs_res   = await db.execute(select(Bug).where(Bug.session_id == session_id))
    bugs: list[Bug] = bugs_res.scalars().all()

    pages_res  = await db.execute(select(PageNode).where(PageNode.session_id == session_id))
    pages: list[PageNode] = pages_res.scalars().all()

    runs_res   = await db.execute(select(TestRun).where(TestRun.session_id == session_id))
    runs: list[TestRun] = runs_res.scalars().all()

    errs_res   = await db.execute(select(ConsoleError).where(ConsoleError.session_id == session_id))
    console_errors: list[ConsoleError] = errs_res.scalars().all()

    net_res    = await db.execute(select(NetworkFailure).where(NetworkFailure.session_id == session_id))
    net_fails: list[NetworkFailure] = net_res.scalars().all()

    report: dict[str, Any] = session.config or {}

    # ── Score individual bugs ────────────────────────────────────────────────
    bug_scores = [_score_bug(b) for b in bugs]

    # ── Compute dimensions ───────────────────────────────────────────────────
    dims: dict[str, DimensionResult] = {
        "coverage":    _dim_coverage(pages, runs, report),
        "bug_quality": _dim_bug_quality(bug_scores),
        "precision":   _dim_precision(bugs, report),
        "detail":      _dim_detail(bugs),
        "severity":    _dim_severity_calibration(bugs),
        "confidence":  _dim_confidence(bugs, report),
        "monitoring":  _dim_monitoring(console_errors, net_fails, pages),
        "persona_div": _dim_persona_diversity(bugs, runs),
    }

    # ── Weighted overall score ───────────────────────────────────────────────
    overall = sum(dims[k].score * WEIGHTS[k] for k in WEIGHTS)
    overall = round(min(max(overall, 0), 100), 2)
    grade   = _grade(overall)

    # ── Collect all feedback items ───────────────────────────────────────────
    all_feedback: list[dict] = []
    for d in dims.values():
        all_feedback.extend(d.feedback_items)
    # Sort by priority descending
    all_feedback.sort(key=lambda x: x.get("priority", 0), reverse=True)

    # ── Ground-truth comparison ──────────────────────────────────────────────
    gt_recall = gt_prec = gt_f1 = gt_found = None
    if expected_bugs:
        gt_recall, gt_prec, gt_f1, gt_found = _ground_truth_eval(bugs, expected_bugs)
        # Inject GT feedback
        if gt_recall < 70:
            all_feedback.insert(0, {
                "dimension": "ground_truth",
                "category": "recall",
                "severity": "critical",
                "priority": 100,
                "title": f"Low Recall: only {gt_recall:.0f}% of expected bugs found",
                "detail": f"The agent found {gt_found} of {len(expected_bugs)} expected bugs.",
                "suggestion": "Consider increasing max_exploration_depth or max_actions_per_page.",
            })

    # ── AI-powered narrative summary ─────────────────────────────────────────
    ai_summary = ""
    if use_ai_summary:
        ai_summary = await _ai_narrative(session, overall, grade, dims, bug_scores, all_feedback)

    result = EvaluationResult(
        session_id=session_id,
        overall_score=overall,
        accuracy_pct=overall,
        grade=grade,
        dimensions=dims,
        bug_scores=bug_scores,
        feedback=all_feedback,
        ai_summary=ai_summary,
        gt_recall_pct=gt_recall,
        gt_precision_pct=gt_prec,
        gt_f1_score=gt_f1,
        gt_found_bugs=gt_found,
    )

    # ── Persist to DB ────────────────────────────────────────────────────────
    await _persist(db, session_id, result, expected_bugs)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Per-bug scoring (0-100)
# ─────────────────────────────────────────────────────────────────────────────

_VAGUE_PATTERNS = re.compile(
    r'^(bug|error|issue|problem|fail|broken|not working|doesn\'t work)\s*$',
    re.IGNORECASE,
)
_SEVERITY_KEYWORDS = {
    "critical": ["crash", "data loss", "security", "exploit", "injection", "xss", "rce",
                 "authentication", "bypass", "unauthorized", "infinite loop", "unresponsive"],
    "high":     ["broken", "fail", "cannot", "blocked", "404", "500", "unavailable",
                 "missing", "error", "exception", "payment", "checkout"],
    "medium":   ["incorrect", "wrong", "unexpected", "inconsistent", "misleading",
                 "slow", "lag", "overlap", "truncat", "overflow"],
    "low":      ["typo", "align", "spacing", "color", "style", "minor", "cosmetic",
                 "tooltip", "label"],
}


def _score_bug(bug: Bug) -> BugScore:
    score = 0.0
    issues: list[str] = []
    suggestions: list[str] = []

    title = (bug.title or "").strip()
    desc  = (bug.description or "").strip()
    exp   = (bug.expected_behavior or "").strip()
    act   = (bug.actual_behavior or "").strip()
    steps = bug.reproduction_steps or []
    sev   = (bug.severity.value if hasattr(bug.severity, "value") else str(bug.severity)).lower()

    # ── Title quality (0-20 pts) ─────────────────────────────────────────────
    if len(title) >= 20 and not _VAGUE_PATTERNS.match(title):
        score += 20
    elif len(title) >= 10:
        score += 12
        if _VAGUE_PATTERNS.match(title):
            issues.append("Title is too generic")
            suggestions.append("Use a specific, descriptive title (e.g. 'Login form submits with empty email field')")
    else:
        score += 5
        issues.append("Title is too short or vague")
        suggestions.append("Provide a clear, specific bug title (15+ characters)")

    # ── Reproduction steps (0-20 pts) ────────────────────────────────────────
    n_steps = len(steps)
    if n_steps >= 4:
        score += 20
    elif n_steps >= 2:
        score += 14
    elif n_steps == 1:
        score += 8
        issues.append("Only 1 reproduction step — hard to reproduce")
        suggestions.append("Add at least 3 reproduction steps to make the bug reproducible")
    else:
        score += 0
        issues.append("No reproduction steps provided")
        suggestions.append("Add numbered reproduction steps so developers can reproduce this")

    # ── Expected behavior (0-12 pts) ─────────────────────────────────────────
    if exp and len(exp) >= 20:
        score += 12
    elif exp:
        score += 7
        issues.append("Expected behavior too brief")
        suggestions.append("Describe expected behavior with more detail (20+ chars)")
    else:
        issues.append("Expected behavior is missing")
        suggestions.append("Add what the correct behavior should be")

    # ── Actual behavior (0-12 pts) ────────────────────────────────────────────
    if act and len(act) >= 20:
        score += 12
    elif act:
        score += 7
        if act == desc:
            issues.append("Actual behavior duplicates description")
    else:
        issues.append("Actual behavior is missing")
        suggestions.append("Describe what actually happened in concrete terms")

    # ── Description quality (0-12 pts) ───────────────────────────────────────
    if desc and len(desc) >= 60:
        score += 12
    elif desc and len(desc) >= 25:
        score += 8
    elif desc:
        score += 4
    else:
        score += 0
        issues.append("Description is empty")

    # ── Severity calibration (0-12 pts) ──────────────────────────────────────
    combined_text = f"{title} {desc} {act}".lower()
    expected_sev = _infer_severity(combined_text)
    if expected_sev == sev:
        score += 12
    elif abs(["low","medium","high","critical"].index(sev) - ["low","medium","high","critical"].index(expected_sev)) <= 1:
        score += 8
    else:
        score += 3
        issues.append(f"Severity '{sev}' may be miscalibrated (text suggests '{expected_sev}')")
        suggestions.append(f"Consider changing severity to '{expected_sev}' based on the bug description")

    # ── Screenshot (0-6 pts) ─────────────────────────────────────────────────
    if bug.screenshot_path:
        score += 6
    else:
        issues.append("No screenshot attached")
        suggestions.append("Screenshots help developers reproduce visual bugs faster")

    # ── Vision detection bonus (0-6 pts) ─────────────────────────────────────
    if bug.detected_by_vision:
        score += 6

    return BugScore(
        bug_id=bug.id,
        title=title,
        score=round(min(max(score, 0), 100), 1),
        issues=issues,
        suggestions=suggestions,
    )


def _infer_severity(text: str) -> str:
    for sev in ("critical", "high", "medium", "low"):
        if any(kw in text for kw in _SEVERITY_KEYWORDS[sev]):
            return sev
    return "medium"


# ─────────────────────────────────────────────────────────────────────────────
# Dimension scorers
# ─────────────────────────────────────────────────────────────────────────────

def _dim_coverage(pages: list, runs: list, report: dict) -> DimensionResult:
    """How broadly was the app explored?"""
    items: list[dict] = []
    n_pages = len(pages)

    # Estimate expected coverage from persona stats
    expected = max(report.get("stats", {}).get("pages_discovered", n_pages), n_pages, 1)
    cov_pct  = min(n_pages / max(expected, 1) * 100, 100)

    # Depth bonus: did at least one persona reach depth >= 3?
    depths = [r.pages_visited or 0 for r in runs]
    max_depth_score = min(max(depths, default=0) / 3 * 40, 40) if depths else 0
    base = cov_pct * 0.60 + max_depth_score

    score = round(min(base, 100), 1)

    if n_pages < 3:
        items.append({
            "dimension": "coverage", "category": "shallow_exploration",
            "severity": "critical", "priority": 90,
            "title": f"Very shallow exploration — only {n_pages} page(s) visited",
            "detail": "The agent visited fewer than 3 pages. Most bugs require navigating the app.",
            "suggestion": "Increase MAX_EXPLORATION_DEPTH (default 4) or check if the URL is publicly accessible.",
        })
    elif n_pages < 8:
        items.append({
            "dimension": "coverage", "category": "limited_coverage",
            "severity": "warning", "priority": 60,
            "title": f"Limited coverage — {n_pages} pages visited",
            "detail": "Less than 8 pages explored. Some areas of the app may be untested.",
            "suggestion": "Consider raising max_exploration_depth or increasing max_actions_per_page.",
        })

    if all(r.pages_visited == 0 for r in runs) and runs:
        items.append({
            "dimension": "coverage", "category": "no_navigation",
            "severity": "critical", "priority": 95,
            "title": "No navigation recorded by any persona",
            "detail": "All test runs report 0 pages visited — the browser may have failed to load.",
            "suggestion": "Check that the target URL is reachable and does not require a login.",
        })

    return DimensionResult("coverage", score, WEIGHTS["coverage"], items)


def _dim_bug_quality(bug_scores: list[BugScore]) -> DimensionResult:
    """Average quality score across all found bugs."""
    items: list[dict] = []
    if not bug_scores:
        return DimensionResult("bug_quality", 50.0, WEIGHTS["bug_quality"], [{
            "dimension": "bug_quality", "category": "no_bugs",
            "severity": "info", "priority": 10,
            "title": "No bugs found",
            "detail": "The session did not detect any bugs. This may mean the app is healthy or coverage was insufficient.",
            "suggestion": "Verify the app is running and interactive elements are accessible.",
        }])

    avg = sum(b.score for b in bug_scores) / len(bug_scores)
    low_quality = [b for b in bug_scores if b.score < 50]
    score = round(avg, 1)

    if low_quality:
        affected = [b.bug_id for b in low_quality]
        items.append({
            "dimension": "bug_quality", "category": "low_quality_reports",
            "severity": "warning", "priority": 70,
            "title": f"{len(low_quality)} bug report(s) are low quality (score < 50)",
            "detail": f"These reports are missing key fields and may be hard to act on: {', '.join(b.title[:40] for b in low_quality[:3])}{'…' if len(low_quality)>3 else ''}",
            "suggestion": "Review and enrich these reports with reproduction steps and clearer expected/actual behavior.",
            "affected_ids": affected,
        })

    perfect = [b for b in bug_scores if b.score >= 90]
    if perfect:
        items.append({
            "dimension": "bug_quality", "category": "high_quality_reports",
            "severity": "info", "priority": 5,
            "title": f"{len(perfect)} bug report(s) are excellent quality",
            "detail": "These reports have comprehensive steps, screenshots, and clear expected/actual behavior.",
            "suggestion": "",
        })

    return DimensionResult("bug_quality", score, WEIGHTS["bug_quality"], items)


def _dim_precision(bugs: list[Bug], report: dict) -> DimensionResult:
    """Estimated true-positive rate — anti-false-positive filter effectiveness."""
    items: list[dict] = []
    if not bugs:
        return DimensionResult("precision", 80.0, WEIGHTS["precision"], [])

    # Heuristics for likely false positives:
    suspicious: list[Bug] = []
    for bug in bugs:
        title = (bug.title or "").lower()
        desc  = (bug.description or "").lower()
        suspect = False

        # Short / vague title
        if _VAGUE_PATTERNS.match(bug.title or ""):
            suspect = True
        # No actual behavior described
        if not (bug.actual_behavior or "").strip():
            suspect = True
        # Confidence below threshold (stored in bug.error_type field when set)
        # Title is just a URL or HTTP status code
        if re.match(r'^https?://', (bug.title or "")):
            suspect = True
        # Analytics / third-party failures treated as bugs
        if any(tp in (bug.url_at_error or "") for tp in ["google-analytics", "hotjar", "segment", "sentry", "analytics"]):
            suspect = True

        if suspect:
            suspicious.append(bug)

    n_suspicious = len(suspicious)
    precision_est = max(0.0, 1.0 - n_suspicious / len(bugs)) * 100
    score = round(precision_est, 1)

    if n_suspicious > 0:
        items.append({
            "dimension": "precision", "category": "suspected_false_positives",
            "severity": "warning", "priority": 75,
            "title": f"{n_suspicious} bug(s) are likely false positives",
            "detail": f"These reports have indicators of false positives (vague title, no actual behavior, third-party URLs): {', '.join((b.title or '')[:35] for b in suspicious[:4])}",
            "suggestion": "Review these bugs manually. Consider tuning the ActionValidator thresholds.",
            "affected_ids": [b.id for b in suspicious],
        })

    return DimensionResult("precision", score, WEIGHTS["precision"], items)


def _dim_detail(bugs: list[Bug]) -> DimensionResult:
    """Completeness of expected/actual behavior fields."""
    items: list[dict] = []
    if not bugs:
        return DimensionResult("detail", 60.0, WEIGHTS["detail"], [])

    def detail_score_one(b: Bug) -> float:
        s = 0.0
        exp_len = len((b.expected_behavior or "").strip())
        act_len = len((b.actual_behavior or "").strip())
        steps   = len(b.reproduction_steps or [])
        if exp_len >= 30: s += 35
        elif exp_len >= 10: s += 20
        if act_len >= 30: s += 35
        elif act_len >= 10: s += 20
        if steps >= 3: s += 30
        elif steps >= 1: s += 18
        return min(s, 100)

    scores = [detail_score_one(b) for b in bugs]
    avg = sum(scores) / len(scores)
    score = round(avg, 1)

    missing_both = [b for b in bugs if not (b.expected_behavior or "").strip() and not (b.actual_behavior or "").strip()]
    if missing_both:
        items.append({
            "dimension": "detail", "category": "missing_expected_actual",
            "severity": "critical", "priority": 85,
            "title": f"{len(missing_both)} bug(s) are missing expected AND actual behavior",
            "detail": "Bugs without expected/actual behavior descriptions are nearly impossible to triage.",
            "suggestion": "Ensure every bug report includes what should happen vs. what actually happened.",
            "affected_ids": [b.id for b in missing_both],
        })

    no_steps = [b for b in bugs if not (b.reproduction_steps or [])]
    if len(no_steps) > len(bugs) * 0.5:
        items.append({
            "dimension": "detail", "category": "missing_steps",
            "severity": "warning", "priority": 65,
            "title": f"{len(no_steps)} bug(s) have no reproduction steps",
            "detail": "More than half the bugs lack step-by-step reproduction instructions.",
            "suggestion": "Add at least 2-3 reproduction steps to every bug to help developers fix them.",
            "affected_ids": [b.id for b in no_steps],
        })

    return DimensionResult("detail", score, WEIGHTS["detail"], items)


def _dim_severity_calibration(bugs: list[Bug]) -> DimensionResult:
    """Are severities appropriately calibrated?"""
    items: list[dict] = []
    if not bugs:
        return DimensionResult("severity", 80.0, WEIGHTS["severity"], [])

    mismatches: list[Bug] = []
    for bug in bugs:
        combined = f"{bug.title or ''} {bug.description or ''} {bug.actual_behavior or ''}".lower()
        inferred = _infer_severity(combined)
        assigned = (bug.severity.value if hasattr(bug.severity, "value") else str(bug.severity)).lower()
        sev_levels = ["low", "medium", "high", "critical"]
        if abs(sev_levels.index(inferred) - sev_levels.index(assigned)) >= 2:
            mismatches.append(bug)

    score = max(0.0, 100 - len(mismatches) / max(len(bugs), 1) * 100)
    score = round(score, 1)

    if mismatches:
        items.append({
            "dimension": "severity", "category": "severity_mismatch",
            "severity": "warning", "priority": 55,
            "title": f"{len(mismatches)} bug(s) have potentially miscalibrated severity",
            "detail": "The text of these bugs suggests a different severity than assigned.",
            "suggestion": "Review severity assignments: 'critical' = crashes/security, 'high' = blocking flows, 'medium' = incorrect behavior, 'low' = cosmetic.",
            "affected_ids": [b.id for b in mismatches],
        })

    # Inflation check: >50% critical is suspicious
    n_critical = sum(1 for b in bugs if (b.severity.value if hasattr(b.severity,"value") else str(b.severity)).lower() == "critical")
    if len(bugs) >= 5 and n_critical / len(bugs) > 0.5:
        items.append({
            "dimension": "severity", "category": "severity_inflation",
            "severity": "warning", "priority": 60,
            "title": f"{n_critical}/{len(bugs)} bugs marked critical — possible severity inflation",
            "detail": "Having more than 50% critical bugs is unusual and may reduce triage effectiveness.",
            "suggestion": "Reserve 'critical' for security issues, data loss, or app crashes.",
        })

    return DimensionResult("severity", score, WEIGHTS["severity"], items)


def _dim_confidence(bugs: list[Bug], report: dict) -> DimensionResult:
    """Average AI confidence across findings."""
    items: list[dict] = []
    # Confidence is not stored per-bug in the DB, but we can infer from
    # the final report's health score and from bug count vs. pages visited.
    health = (report.get("overall_health") or "healthy").lower()
    health_map = {"healthy": 85, "degraded": 70, "broken": 60, "critical": 50}
    base_conf = health_map.get(health, 70)

    # If many bugs with no screenshots and no vision, lower confidence slightly
    bugs_no_evidence = sum(1 for b in bugs if not b.screenshot_path and not b.detected_by_vision)
    if bugs and bugs_no_evidence / len(bugs) > 0.7:
        base_conf -= 8
        items.append({
            "dimension": "confidence", "category": "low_evidence",
            "severity": "info", "priority": 30,
            "title": f"{bugs_no_evidence} bug(s) lack visual evidence (screenshot or Vision)",
            "detail": "Bugs without screenshots rely entirely on text analysis, reducing verifiability.",
            "suggestion": "Enable VISION_ENABLED=true and ensure SCREENSHOTS_DIR is writable.",
        })

    score = round(min(max(base_conf, 0), 100), 1)
    return DimensionResult("confidence", score, WEIGHTS["confidence"], items)


def _dim_monitoring(
    console_errors: list, net_fails: list, pages: list
) -> DimensionResult:
    """Did the agent capture runtime errors and network failures?"""
    items: list[dict] = []
    n_pages = max(len(pages), 1)

    # Expect at least some monitoring events per page on a real app
    total_events = len(console_errors) + len(net_fails)
    density = total_events / n_pages

    if density >= 0.5:
        score = 100.0
    elif density >= 0.1:
        score = 70.0
    else:
        score = 40.0
        if n_pages >= 3:
            items.append({
                "dimension": "monitoring", "category": "no_runtime_errors",
                "severity": "info", "priority": 20,
                "title": "Very few runtime errors or network failures captured",
                "detail": f"Only {total_events} monitoring events across {n_pages} pages.",
                "suggestion": "This may indicate a very clean app — or that monitoring hooks aren't firing. Check PageInterceptor setup.",
            })

    return DimensionResult("monitoring", round(score, 1), WEIGHTS["monitoring"], items)


def _dim_persona_diversity(bugs: list[Bug], runs: list) -> DimensionResult:
    """Did the three personas find distinct bugs?"""
    items: list[dict] = []
    if not bugs:
        return DimensionResult("persona_div", 60.0, WEIGHTS["persona_div"], [])

    persona_sets: dict[str, set] = {}
    for bug in bugs:
        p = bug.persona_name or "unknown"
        persona_sets.setdefault(p, set()).add(bug.title[:60].lower())

    active_personas = len(persona_sets)
    if active_personas == 0:
        return DimensionResult("persona_div", 50.0, WEIGHTS["persona_div"], [])

    # Overlap: how many bug titles appear in more than one persona?
    all_titles = [t for ts in persona_sets.values() for t in ts]
    title_counts = {t: sum(1 for ts in persona_sets.values() if t in ts) for t in all_titles}
    unique_ratio = sum(1 for c in title_counts.values() if c == 1) / max(len(all_titles), 1)

    # More active personas = better diversity
    persona_bonus = (active_personas / 3) * 40
    uniqueness_score = unique_ratio * 60
    score = round(min(persona_bonus + uniqueness_score, 100), 1)

    if active_personas == 1:
        items.append({
            "dimension": "persona_div", "category": "single_persona",
            "severity": "warning", "priority": 40,
            "title": "Only 1 persona contributed bugs",
            "detail": "Persona diversity is key to finding different bug classes. Only 1 persona recorded findings.",
            "suggestion": "Ensure MAX_CONCURRENT_PERSONAS >= 3 and check test run statuses.",
        })

    return DimensionResult("persona_div", score, WEIGHTS["persona_div"], items)


# ─────────────────────────────────────────────────────────────────────────────
# Ground-truth comparison
# ─────────────────────────────────────────────────────────────────────────────

def _fuzzy_match(a: str, b: str, threshold: float = 0.55) -> bool:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= threshold


def _ground_truth_eval(
    found_bugs: list[Bug],
    expected: list[str],
) -> tuple[float, float, float, int]:
    """Return (recall%, precision%, f1%, matched_count)."""
    found_titles = [(b.id, b.title or "") for b in found_bugs]
    tp = 0
    matched_exp = set()
    matched_found = set()

    for exp_title in expected:
        for bug_id, found_title in found_titles:
            if bug_id not in matched_found and _fuzzy_match(exp_title, found_title):
                tp += 1
                matched_exp.add(exp_title)
                matched_found.add(bug_id)
                break

    recall    = (tp / len(expected) * 100) if expected else 100.0
    precision = (tp / len(found_bugs) * 100) if found_bugs else 0.0
    f1        = (2 * recall * precision / (recall + precision)) if (recall + precision) > 0 else 0.0
    return round(recall, 1), round(precision, 1), round(f1, 1), tp


# ─────────────────────────────────────────────────────────────────────────────
# AI narrative summary
# ─────────────────────────────────────────────────────────────────────────────

async def _ai_narrative(
    session: Session,
    overall: float,
    grade: str,
    dims: dict[str, DimensionResult],
    bug_scores: list[BugScore],
    feedback: list[dict],
) -> str:
    try:
        from ai.client import reason

        dim_lines = "\n".join(
            f"  - {name}: {d.score:.0f}/100" for name, d in dims.items()
        )
        top_feedback = "\n".join(
            f"  [{i+1}] [{f['severity'].upper()}] {f['title']}"
            for i, f in enumerate(feedback[:5])
        )
        low_bugs = [b for b in bug_scores if b.score < 55]
        low_bug_lines = "\n".join(
            f"  - '{b.title[:60]}' (score {b.score:.0f}): {'; '.join(b.issues[:2])}"
            for b in low_bugs[:4]
        )

        prompt = f"""You are an expert QA engineer reviewing the results of an automated AI QA session.

Session URL: {session.url}
Overall accuracy score: {overall:.1f}% (Grade {grade})

Dimension scores:
{dim_lines}

Top feedback items:
{top_feedback}

Low-quality bug reports:
{low_bug_lines or '  (none)'}

Write a concise 3-4 sentence professional evaluation summary that:
1. States the overall quality grade and what it means
2. Highlights the 1-2 most important strengths
3. Identifies the 1-2 most critical weaknesses to fix
4. Gives one specific, actionable next step

Be direct and specific. Do not repeat the scores verbatim."""

        result = await reason(prompt, session_id=session.id)
        return result.get("content", result.get("result", "")) if isinstance(result, dict) else str(result)
    except Exception as exc:
        logger.warning("AI narrative generation failed: %s", exc)
        return (
            f"Session scored {overall:.1f}% (Grade {grade}). "
            f"Coverage: {dims['coverage'].score:.0f}%, "
            f"Bug Quality: {dims['bug_quality'].score:.0f}%, "
            f"Precision: {dims['precision'].score:.0f}%. "
            f"Review the feedback items below for specific improvement actions."
        )


# ─────────────────────────────────────────────────────────────────────────────
# DB persistence
# ─────────────────────────────────────────────────────────────────────────────

async def _persist(
    db: AsyncSession,
    session_id: str,
    result: EvaluationResult,
    expected_bugs: list[str] | None,
) -> None:
    # Upsert evaluation row
    ev_res = await db.execute(select(Evaluation).where(Evaluation.session_id == session_id))
    ev: Evaluation | None = ev_res.scalar_one_or_none()
    if not ev:
        ev = Evaluation(session_id=session_id)
        db.add(ev)

    ev.status          = EvaluationStatus.completed
    ev.overall_score   = result.overall_score
    ev.accuracy_pct    = result.accuracy_pct
    ev.grade           = result.grade
    ev.dim_coverage    = result.dimensions["coverage"].score
    ev.dim_bug_quality = result.dimensions["bug_quality"].score
    ev.dim_precision   = result.dimensions["precision"].score
    ev.dim_detail      = result.dimensions["detail"].score
    ev.dim_severity    = result.dimensions["severity"].score
    ev.dim_confidence  = result.dimensions["confidence"].score
    ev.dim_monitoring  = result.dimensions["monitoring"].score
    ev.dim_persona_div = result.dimensions["persona_div"].score
    ev.bug_scores      = [
        {"bug_id": b.bug_id, "title": b.title, "score": b.score,
         "issues": b.issues, "suggestions": b.suggestions}
        for b in result.bug_scores
    ]
    ev.gt_expected_bugs = len(expected_bugs) if expected_bugs else None
    ev.gt_found_bugs    = result.gt_found_bugs
    ev.gt_recall_pct    = result.gt_recall_pct
    ev.gt_precision_pct = result.gt_precision_pct
    ev.gt_f1_score      = result.gt_f1_score
    ev.ai_summary       = result.ai_summary
    ev.completed_at     = datetime.utcnow()

    await db.flush()

    # Replace feedback items
    old_fb = await db.execute(
        select(EvaluationFeedback).where(EvaluationFeedback.evaluation_id == ev.id)
    )
    for fb in old_fb.scalars().all():
        await db.delete(fb)
    await db.flush()

    for item in result.feedback:
        db.add(EvaluationFeedback(
            evaluation_id=ev.id,
            dimension=item.get("dimension", "general"),
            category=item.get("category", "general"),
            severity=item.get("severity", "info"),
            priority=item.get("priority", 0),
            title=item.get("title", ""),
            detail=item.get("detail", ""),
            suggestion=item.get("suggestion", ""),
            affected_ids=item.get("affected_ids", []),
        ))

    await db.commit()
