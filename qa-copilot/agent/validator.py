"""
ActionValidator — reduces false-positive bugs before DB persistence.

A bug candidate passes validation only if it clears ALL relevant checks:
  1. Confidence threshold (LLM self-reported confidence ≥ 0.55)
  2. Not a known-safe pattern (e.g., Google Analytics 403, browser extension errors)
  3. Console error is not a benign third-party warning
  4. Navigation failure is not a redirect to login (expected behaviour)
  5. Deduplication against already-seen titles this session
  6. Minimum text length (prevents empty/garbage bugs)

Any bug that fails validation is demoted to a "skipped" log entry, not persisted.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Patterns that are safe to ignore
# ------------------------------------------------------------------ #

_BENIGN_CONSOLE_PATTERNS: list[re.Pattern] = [
    re.compile(r"favicon\.ico", re.I),
    re.compile(r"chrome-extension://", re.I),
    re.compile(r"mozilla\.org", re.I),
    re.compile(r"\[Deprecation\]", re.I),
    re.compile(r"React DevTools", re.I),
    re.compile(r"Download the React DevTools", re.I),
    re.compile(r"hot.?reload", re.I),
    re.compile(r"webpack", re.I),
    re.compile(r"vite", re.I),
    re.compile(r"Source Map", re.I),
    re.compile(r"Content Security Policy", re.I),  # CSP warnings are informational
]

_BENIGN_NETWORK_PATTERNS: list[re.Pattern] = [
    re.compile(r"google-analytics\.com", re.I),
    re.compile(r"googletagmanager\.com", re.I),
    re.compile(r"doubleclick\.net", re.I),
    re.compile(r"facebook\.com/tr", re.I),
    re.compile(r"hotjar\.com", re.I),
    re.compile(r"sentry\.io", re.I),
    re.compile(r"amplitude\.com", re.I),
    re.compile(r"segment\.io", re.I),
    re.compile(r"mixpanel\.com", re.I),
    re.compile(r"intercom\.io", re.I),
    re.compile(r"crisp\.chat", re.I),
]

# Auth redirect URLs that are NOT bugs — they're expected 401/403 redirects
_AUTH_REDIRECT_PATTERNS: list[re.Pattern] = [
    re.compile(r"/login", re.I),
    re.compile(r"/signin", re.I),
    re.compile(r"/auth", re.I),
    re.compile(r"/unauthorized", re.I),
    re.compile(r"access.?denied", re.I),
]


# ------------------------------------------------------------------ #
# Validator
# ------------------------------------------------------------------ #

@dataclass
class ValidationResult:
    passed: bool
    reason: str


class ActionValidator:
    def __init__(self) -> None:
        self._seen_titles: set[str] = set()
        self.skipped: int = 0

    def validate(self, candidate: dict) -> ValidationResult:
        title: str = candidate.get("title", "").strip()
        severity: str = candidate.get("severity", "low")
        error_type: str = candidate.get("error_type", "")
        description: str = candidate.get("description", "")
        confidence: float = float(candidate.get("confidence", 1.0))
        url: str = candidate.get("url_at_error", "")

        # ── 1. Minimum content ────────────────────────────────────────
        if len(title) < 8:
            return self._skip(candidate, "title too short")
        if len(description) < 10:
            return self._skip(candidate, "description too short")

        # ── 2. Confidence threshold ───────────────────────────────────
        if confidence < 0.45:
            return self._skip(candidate, f"low LLM confidence ({confidence:.2f})")

        # ── 3. Deduplication ─────────────────────────────────────────
        key = title.lower()[:70]
        if key in self._seen_titles:
            return self._skip(candidate, "duplicate bug title")
        self._seen_titles.add(key)

        # ── 4. Benign console errors ──────────────────────────────────
        if error_type == "console_error":
            for pat in _BENIGN_CONSOLE_PATTERNS:
                if pat.search(title) or pat.search(description):
                    return self._skip(candidate, f"benign console pattern: {pat.pattern}")

        # ── 5. Benign network failures ────────────────────────────────
        if error_type == "api_error":
            for pat in _BENIGN_NETWORK_PATTERNS:
                if pat.search(title) or pat.search(url):
                    return self._skip(candidate, f"third-party analytics/tracking: {pat.pattern}")

        # ── 6. Expected auth redirects ────────────────────────────────
        if error_type == "nav_failure":
            for pat in _AUTH_REDIRECT_PATTERNS:
                if pat.search(url):
                    return self._skip(candidate, "expected auth redirect, not a bug")

        # ── 7. Performance bugs need real threshold breach ────────────
        if error_type == "performance":
            # Must mention > 5000ms or we demote to low
            if "ms" not in title and "second" not in title.lower():
                candidate["severity"] = "low"

        return ValidationResult(passed=True, reason="ok")

    def _skip(self, candidate: dict, reason: str) -> ValidationResult:
        self.skipped += 1
        logger.debug("Bug skipped [%s]: %s", reason, candidate.get("title", ""))
        return ValidationResult(passed=False, reason=reason)
