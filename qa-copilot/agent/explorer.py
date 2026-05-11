"""
PersonaExplorer — drives one AI persona through a web application.

Upgrades:
  • EventEmitter integration — every significant action is broadcast live
  • Claude Vision analysis — screenshots sent to vision model for visual bugs
  • Cancellation checks — respects session.status == "cancelling"
  • Token tracking — session_id passed to AI calls
  • visual_quality stored on page nodes
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from urllib.parse import urlparse

from ai.client import reason
from ai.prompts import (
    page_analysis_prompt,
    bug_assessment_prompt,
    exploration_strategy_prompt,
    selector_recovery_prompt,
    accessibility_audit_prompt,
)
from agent.events import EventEmitter
from agent.validator import ActionValidator
from agent.vision import analyse_screenshot
from browser.driver import BrowserDriver
from config import settings
from personas.engine import PersonaEngine

logger = logging.getLogger(__name__)

_MAX_SELECTOR_FAILURES = 3
_CANCELLED_STATUSES = {"cancelling", "cancelled"}


class PersonaExplorer:
    def __init__(
        self,
        session_id: str,
        target_url: str,
        persona: dict[str, Any],
        persona_engine: PersonaEngine,
        emitter: EventEmitter | None = None,
    ) -> None:
        self.session_id = session_id
        self.target_url = target_url
        self.persona = persona
        self.persona_engine = persona_engine
        self.emitter = emitter
        self.driver = BrowserDriver(session_id, persona["name"])
        self.nav_graph = persona_engine.get_graph(persona["name"])
        self.validator = ActionValidator()

        self.visited_urls: set[str] = set()
        self.bug_candidates: list[dict[str, Any]] = []
        self.page_nodes: list[dict[str, Any]] = []
        self.console_errors_all: list[Any] = []
        self.network_failures_all: list[Any] = []
        self._cancelled = False

    async def run(self) -> dict[str, Any]:
        await self.driver.start()
        try:
            await self._explore_url(self.target_url, depth=0)
        except Exception as exc:
            logger.error(
                "Explorer crashed [persona=%s]: %s", self.persona["name"], exc, exc_info=True
            )
        finally:
            await self.driver.stop()

        logger.info(
            "[%s] Done. pages=%d bugs=%d skipped=%d",
            self.persona["name"],
            len(self.visited_urls),
            len(self.bug_candidates),
            self.validator.skipped,
        )
        return {
            "persona": self.persona["name"],
            "bug_candidates": self.bug_candidates,
            "page_nodes": self.page_nodes,
            "console_errors": self.console_errors_all,
            "network_failures": self.network_failures_all,
            "pages_visited": len(self.visited_urls),
            "bugs_skipped_by_validator": self.validator.skipped,
        }

    # ── Core exploration loop ────────────────────────────────────────────

    async def _explore_url(self, url: str, depth: int) -> None:
        if self._cancelled:
            return
        if depth > settings.max_exploration_depth:
            return
        if url in self.visited_urls:
            return
        if not self._same_origin(url):
            return

        self.visited_urls.add(url)
        pname = self.persona["name"]
        logger.info("[%s] depth=%d  %s", pname, depth, url)

        nav = await self.driver.navigate(url)
        if not nav["success"]:
            self._emit_bug(self._nav_failure_bug(url, nav.get("error", "timeout")))
            return

        actual_url = nav["url"]
        load_ms = nav["load_time_ms"]
        vitals = nav.get("vitals", {})

        # Performance check
        if load_ms > 5_000 or vitals.get("fcp", 0) > 4_000:
            ss = await self.driver.screenshot("perf")
            self._emit_bug(self._perf_bug(actual_url, load_ms, vitals, ss))

        dom_summary = await self.driver.extract_dom_summary()
        all_links_raw = await self.driver.get_all_links()
        page_state = await self.driver.get_page_state()

        # Take a page screenshot for vision analysis (depth ≤ 2 only to save cost)
        page_screenshot = ""
        visual_quality = None
        if settings.vision_enabled and depth <= 2:
            page_screenshot = await self.driver.screenshot(f"page_d{depth}")
            vision_issues = await analyse_screenshot(page_screenshot, actual_url, pname)
            if vision_issues:
                visual_quality = "degraded"
                await self._emit_async("vision_insight", pname, actual_url, [i.title for i in vision_issues])
                for vi in vision_issues:
                    self._emit_bug({
                        "title": f"[Visual] {vi.title}",
                        "severity": vi.severity,
                        "confidence": 0.80,
                        "description": vi.description,
                        "reproduction_steps": [f"Navigate to {actual_url}", "Observe the UI"],
                        "expected_behavior": vi.expected,
                        "actual_behavior": vi.actual,
                        "screenshot_path": page_screenshot,
                        "url_at_error": actual_url,
                        "error_type": "ux_issue",
                        "persona_name": pname,
                        "detected_by_vision": True,
                    })

        # AI page analysis
        analysis = await reason(
            page_analysis_prompt(
                url=actual_url,
                title=page_state["title"],
                dom_summary=dom_summary,
                persona=self.persona,
                previously_found_bugs=[b["title"] for b in self.bug_candidates],
            ),
            session_id=self.session_id,
        )

        nav_targets: list[dict] = []

        if analysis:
            page_type = analysis.get("page_type", "other")
            self.nav_graph.record_visit(actual_url, page_state["title"], page_type)

            self.page_nodes.append({
                "url": actual_url,
                "title": page_state["title"],
                "page_type": page_type,
                "interactive_elements": dom_summary.split("\n")[:30],
                "outgoing_links": [lnk["url"] for lnk in all_links_raw[:20]],
                "load_time_ms": load_ms,
                "visual_quality": visual_quality,
            })

            # Emit page_visited event
            if self.emitter:
                await self.emitter.page_visited(actual_url, page_state["title"], page_type, pname, load_ms)

            # Accessibility audit on top-level pages
            if depth <= 1:
                await self._run_accessibility_audit(actual_url, dom_summary)

            # Log AI suspicions
            for s in analysis.get("suspicions", []):
                if s.get("severity") in ("high", "critical") and self.emitter:
                    await self.emitter.log(f"Suspicion: {s.get('observation','')}", persona=pname)

            # Execute test scenarios
            scenarios = sorted(
                analysis.get("test_scenarios", []),
                key=lambda sc: -sc.get("priority", 0),
            )
            max_scenarios = max(1, settings.max_actions_per_page // 3)
            for scenario in scenarios[:max_scenarios]:
                if len(self.bug_candidates) >= settings.max_bugs_per_session:
                    break
                if self._cancelled:
                    break
                await self._run_scenario(scenario, actual_url, dom_summary)

            nav_targets = analysis.get("navigation_targets", [])
            if nav_targets and isinstance(nav_targets[0], str):
                nav_targets = [{"url": u, "rationale": "", "priority": 3} for u in nav_targets]

        # Merge discovered links
        for link in all_links_raw:
            if not any(t.get("url") == link["url"] for t in nav_targets):
                nav_targets.append({"url": link["url"], "rationale": link.get("text", ""), "priority": 2})

        # Flush intercepted errors
        console_errors, net_failures = self.driver.flush_interceptor()
        self.console_errors_all.extend(console_errors)
        self.network_failures_all.extend(net_failures)

        for err in console_errors:
            if err.error_type == "error":
                ss = await self.driver.screenshot("console_err")
                self._emit_bug(self._console_error_bug(actual_url, err, ss))

        for fail in net_failures:
            if fail.status_code and fail.status_code >= 500:
                ss = await self.driver.screenshot("api_err")
                self._emit_bug(self._api_failure_bug(actual_url, fail, ss))

        # Emit progress update
        if self.emitter:
            await self.emitter.progress(pname, len(self.visited_urls), len(self.bug_candidates), depth)

        # AI exploration strategy
        strategy = await reason(
            exploration_strategy_prompt(
                visited_urls=list(self.visited_urls),
                discovered_links=nav_targets,
                bugs_so_far=[b["title"] for b in self.bug_candidates],
                persona=self.persona,
                depth=depth,
                max_depth=settings.max_exploration_depth,
                nav_graph_summary=self.nav_graph.summary(),
            ),
            session_id=self.session_id,
        )

        if strategy.get("should_stop"):
            logger.info("[%s] Stopping: %s", pname, strategy.get("stop_reason", ""))
            return

        for next_url in strategy.get("next_urls", [])[:3]:
            if next_url and next_url not in self.visited_urls and not self._cancelled:
                self.nav_graph.record_transition(actual_url, next_url)
                await self._explore_url(next_url, depth + 1)

    # ── Scenario execution ───────────────────────────────────────────────

    async def _run_scenario(self, scenario: dict, page_url: str, dom_summary: str) -> None:
        actions = scenario.get("actions", [])
        expected = scenario.get("expected_outcome", "")
        failure_indicators = scenario.get("failure_indicators", [])
        steps_taken: list[str] = []
        selector_failures = 0

        for action in actions[:settings.max_actions_per_page]:
            if self._cancelled:
                break
            result = await self.driver.execute_action(action)
            desc = action.get("description") or f"{action.get('type')} {action.get('target','')}"
            steps_taken.append(f"{'✓' if result['success'] else '✗'} {desc}")

            if not result["success"] and not action.get("optional") and action.get("target"):
                selector_failures += 1
                if selector_failures <= _MAX_SELECTOR_FAILURES:
                    recovery = await reason(
                        selector_recovery_prompt(
                            failed_selector=action["target"],
                            action_type=action.get("type", "click"),
                            dom_snapshot=dom_summary,
                            description=desc,
                        ),
                        session_id=self.session_id,
                    )
                    if recovery.get("element_found") and recovery.get("selectors"):
                        for sel_obj in recovery["selectors"]:
                            if sel_obj.get("confidence", 0) >= 0.6:
                                retry = await self.driver.execute_action({**action, "target": sel_obj["selector"]})
                                if retry["success"]:
                                    steps_taken[-1] = f"✓ {desc} [recovered]"
                                    selector_failures -= 1
                                    break

            if selector_failures >= _MAX_SELECTOR_FAILURES:
                steps_taken.append("✗ Aborted: too many selector failures")
                break

            await asyncio.sleep(self.persona.get("interaction_delay_ms", 200) / 1000)

        console_errors, net_failures = self.driver.flush_interceptor()
        page_state = await self.driver.get_page_state()

        assessment = await reason(
            bug_assessment_prompt(
                action_taken=f"Scenario '{scenario.get('name','unnamed')}': " + "; ".join(steps_taken[-8:]),
                expected=expected,
                actual_state=page_state.get("visible_text_snippet", ""),
                console_errors=[f"[{e.error_type}] {e.message}" for e in console_errors],
                network_failures=[f"{f.method or 'GET'} {f.request_url} → {f.status_code or 'FAILED'}" for f in net_failures],
                persona=self.persona,
                failure_indicators=failure_indicators,
                page_url=page_url,
            ),
            session_id=self.session_id,
        )

        if assessment.get("is_bug"):
            ss = await self.driver.screenshot(f"bug_{assessment.get('error_type','issue')}")
            candidate = {
                "title": assessment.get("title", "Unnamed bug"),
                "severity": assessment.get("severity", "medium"),
                "confidence": float(assessment.get("confidence", 0.8)),
                "description": assessment.get("description", ""),
                "reproduction_steps": steps_taken,
                "expected_behavior": assessment.get("expected_behavior", expected),
                "actual_behavior": assessment.get("actual_behavior", ""),
                "screenshot_path": ss,
                "url_at_error": page_state["url"],
                "error_type": assessment.get("error_type", "assertion"),
                "persona_name": self.persona["name"],
            }
            self._emit_bug(candidate)

        self.console_errors_all.extend(console_errors)
        self.network_failures_all.extend(net_failures)

    # ── Accessibility audit ──────────────────────────────────────────────

    async def _run_accessibility_audit(self, url: str, dom_summary: str) -> None:
        audit = await reason(
            accessibility_audit_prompt(url=url, dom_summary=dom_summary),
            max_tokens=1024,
            session_id=self.session_id,
        )
        for issue in (audit or {}).get("issues", []):
            if issue.get("severity") in ("high", "critical"):
                self._emit_bug({
                    "title": f"[A11Y] {issue.get('rule','WCAG')}: {issue.get('description','')[:70]}",
                    "severity": issue.get("severity", "medium"),
                    "confidence": 0.85,
                    "description": f"{issue.get('description','')} Fix: {issue.get('fix','')}",
                    "reproduction_steps": [f"Navigate to {url}", f"Inspect: {issue.get('element','')}"],
                    "expected_behavior": "Meets WCAG 2.1 AA",
                    "actual_behavior": issue.get("description", ""),
                    "screenshot_path": "",
                    "url_at_error": url,
                    "error_type": "accessibility",
                    "persona_name": self.persona["name"],
                })

    # ── Bug emission ─────────────────────────────────────────────────────

    def _emit_bug(self, candidate: dict) -> None:
        validation = self.validator.validate(candidate)
        if not validation.passed:
            return
        self.bug_candidates.append(candidate)
        pname = self.persona["name"]
        logger.info("[%s] Bug [%s]: %s", pname, candidate.get("severity","?").upper(), candidate.get("title","")[:80])
        if self.emitter:
            asyncio.ensure_future(
                self.emitter.bug_found(
                    candidate["title"],
                    candidate.get("severity", "medium"),
                    pname,
                    candidate.get("url_at_error", ""),
                )
            )

    async def _emit_async(self, kind: str, persona: str, url: str, issues: list[str]) -> None:
        if self.emitter:
            await self.emitter.vision_insight(persona, url, issues)

    # ── Bug factories ────────────────────────────────────────────────────

    def _nav_failure_bug(self, url: str, error: str) -> dict:
        return {
            "title": f"Page unreachable: {url[:70]}",
            "severity": "high", "confidence": 0.95,
            "description": f"Navigation to {url} failed. Error: {error}",
            "reproduction_steps": [f"Navigate to {url}"],
            "expected_behavior": "Page loads with HTTP 200",
            "actual_behavior": f"Navigation failed: {error}",
            "screenshot_path": "", "url_at_error": url,
            "error_type": "nav_failure", "persona_name": self.persona["name"],
        }

    def _perf_bug(self, url: str, load_ms: float, vitals: dict, screenshot: str) -> dict:
        fcp = vitals.get("fcp")
        detail = f"Load: {load_ms:.0f}ms" + (f", FCP: {fcp}ms" if fcp else "")
        return {
            "title": f"Slow page load: {url[:60]} ({load_ms:.0f}ms)",
            "severity": "medium" if load_ms < 8_000 else "high", "confidence": 0.9,
            "description": f"Page load exceeded threshold. {detail}.",
            "reproduction_steps": [f"Navigate to {url}", "Measure load time"],
            "expected_behavior": "Page loads within 3 seconds",
            "actual_behavior": f"Page took {load_ms:.0f}ms ({detail})",
            "screenshot_path": screenshot, "url_at_error": url,
            "error_type": "performance", "persona_name": self.persona["name"],
        }

    def _console_error_bug(self, url: str, error: Any, screenshot: str) -> dict:
        return {
            "title": f"Console error: {error.message[:70]}",
            "severity": "medium", "confidence": 0.75,
            "description": f"Browser console error on {url}: {error.message}",
            "reproduction_steps": [f"Navigate to {url}", "Open DevTools → Console"],
            "expected_behavior": "No console errors",
            "actual_behavior": f"console.error: {error.message}",
            "screenshot_path": screenshot, "url_at_error": url,
            "error_type": "console_error", "persona_name": self.persona["name"],
        }

    def _api_failure_bug(self, url: str, failure: Any, screenshot: str) -> dict:
        return {
            "title": f"API {failure.status_code}: {failure.request_url[:60]}",
            "severity": "high" if failure.status_code >= 500 else "medium", "confidence": 0.9,
            "description": f"HTTP {failure.status_code} on {failure.method or 'GET'} {failure.request_url}",
            "reproduction_steps": [f"Navigate to {url}", f"Trigger call to {failure.request_url}"],
            "expected_behavior": "API returns 2xx",
            "actual_behavior": f"HTTP {failure.status_code}",
            "screenshot_path": screenshot, "url_at_error": url,
            "error_type": "api_error", "persona_name": self.persona["name"],
        }

    def _same_origin(self, url: str) -> bool:
        try:
            return urlparse(self.target_url).netloc == urlparse(url).netloc
        except Exception:
            return False
