"""
QA Copilot prompt library.

Design principles:
- Every prompt ends with a strict JSON schema so the LLM never drifts into prose.
- Chain-of-thought is embedded ("reason_trace" field) to improve accuracy.
- Persona psychology is injected per-call so the model never forgets its role.
- Prompts are written to elicit *specificity*: selectors, not vague descriptions.
"""

# ------------------------------------------------------------------ #
# System prompt — cached across all calls in a session
# ------------------------------------------------------------------ #

SYSTEM_PROMPT = """You are QA Copilot — an elite, autonomous QA engineer running inside a real browser.

Your core philosophy:
• You think like the most experienced human QA engineer alive.
• You NEVER hardcode assumptions. You infer everything from what you see.
• You catch bugs other tools miss: race conditions, misleading copy, silent failures,
  accessibility traps, mobile-hostile layouts, inconsistent error messages.
• You reason in steps before acting. Surface specifics, not vague generalities.
• You always output valid JSON matching the exact schema requested. No markdown prose.
• Selectors you write must be real CSS selectors that will actually match DOM elements.

Severity rubric you must apply consistently:
  critical → App crash, data loss, auth bypass, complete flow blocker, security exposure
  high     → Feature broken for most users, major UX failure, misleading error message
  medium   → Feature partially broken, degraded experience, non-obvious friction
  low      → Cosmetic glitch, minor copy issue, insignificant UX friction

When in doubt about severity, escalate — a false critical is safer than a missed one."""


# ------------------------------------------------------------------ #
# Page analysis — primary AI call per page
# ------------------------------------------------------------------ #

def page_analysis_prompt(
    url: str,
    title: str,
    dom_summary: str,
    persona: dict,
    previously_found_bugs: list[str] | None = None,
) -> str:
    bug_context = ""
    if previously_found_bugs:
        bug_context = f"\nBugs already logged this session (avoid duplicates):\n" + \
                      "\n".join(f"  - {b}" for b in previously_found_bugs[:10])

    return f"""PERSONA: {persona['name']}
PERSONA PROFILE: {persona['description']}
BEHAVIORAL STYLE: {persona['style']}
TEST FOCUS: {persona['test_focus']}
{bug_context}

CURRENT PAGE:
  URL:   {url}
  Title: {title}

DOM SUMMARY (interactive elements extracted from live page):
{dom_summary}

TASK:
1. Identify what this page is for and what flows a user can take.
2. Design concrete, executable test scenarios tailored to the {persona['name']} persona.
3. For every form field, specify realistic selectors (prefer: #id, [name=x], [placeholder="x"], input[type=x]).
4. Identify anything that looks suspicious or poorly implemented.

Think step by step first, then output ONLY this JSON:
{{
  "reason_trace": "<3-5 sentences of QA reasoning before acting>",
  "page_type": "<login|signup|dashboard|form|landing|navigation|settings|error|modal|checkout|profile|other>",
  "page_purpose": "<one precise sentence>",
  "authentication_required": <true|false>,
  "key_user_flows": ["<flow 1>", "<flow 2>"],
  "test_scenarios": [
    {{
      "name": "<concise scenario name>",
      "priority": <1-5>,
      "category": "<auth|navigation|form_validation|ui_interaction|api|accessibility|performance>",
      "actions": [
        {{
          "type": "<click|fill|select|navigate|hover|submit|check|uncheck|press_key|wait|assert_text|assert_visible>",
          "target": "<precise CSS selector or URL for navigate>",
          "value": "<string value for fill/select/press_key/assert_text, null otherwise>",
          "description": "<what this action does and why>",
          "optional": <true|false>
        }}
      ],
      "expected_outcome": "<specific, measurable expected result>",
      "failure_indicators": ["<text or state that would indicate failure>"],
      "risk_areas": ["<specific concern>"]
    }}
  ],
  "navigation_targets": [
    {{"url": "<url>", "rationale": "<why to visit this next>", "priority": <1-5>}}
  ],
  "suspicions": [
    {{"observation": "<what looks wrong>", "potential_bug": "<what bug this could be>", "severity": "<low|medium|high|critical>"}}
  ],
  "accessibility_flags": ["<any obvious a11y issues spotted in DOM>"]
}}

Prioritize scenarios by impact. The {persona['name']} persona emphasizes: {persona['test_focus']}."""


# ------------------------------------------------------------------ #
# Bug assessment — called after each scenario execution
# ------------------------------------------------------------------ #

def bug_assessment_prompt(
    action_taken: str,
    expected: str,
    actual_state: str,
    console_errors: list[str],
    network_failures: list[str],
    persona: dict,
    failure_indicators: list[str] | None = None,
    page_url: str = "",
) -> str:
    indicators_block = ""
    if failure_indicators:
        indicators_block = "Pre-identified failure indicators:\n" + \
                           "\n".join(f"  - {f}" for f in failure_indicators)

    return f"""PERSONA: {persona['name']} — {persona['description']}
PAGE URL: {page_url}

WHAT HAPPENED:
  Actions:  {action_taken}
  Expected: {expected}
  {indicators_block}

OBSERVED STATE AFTER ACTIONS:
{actual_state[:600]}

CONSOLE ERRORS ({len(console_errors)}):
{chr(10).join(f'  [{i+1}] {e}' for i, e in enumerate(console_errors[:5])) or '  none'}

NETWORK FAILURES ({len(network_failures)}):
{chr(10).join(f'  [{i+1}] {f}' for i, f in enumerate(network_failures[:5])) or '  none'}

TASK:
1. Determine if the observed outcome represents a real bug vs expected behaviour.
2. Classify precisely. Use the severity rubric strictly.
3. Write reproduction steps that any engineer could follow tomorrow.
4. Be concrete: quote actual error messages, actual UI state, actual HTTP codes.
5. Do NOT flag missing optional features as bugs. Only flag broken or misleading behaviour.

Output ONLY this JSON:
{{
  "reason_trace": "<3-5 sentences: is this a bug, why, how severe>",
  "is_bug": <true|false>,
  "confidence": <0.0-1.0>,
  "title": "<≤80 char concise bug title>",
  "severity": "<low|medium|high|critical>",
  "error_type": "<console_error|nav_failure|form_validation|api_error|ux_issue|accessibility|performance|security>",
  "description": "<2-3 sentence detailed description>",
  "expected_behavior": "<precise expected outcome>",
  "actual_behavior": "<precise observed outcome, quote exact errors/text>",
  "reproduction_steps": [
    "<step 1: navigate to ...>",
    "<step 2: ...>"
  ],
  "affected_users": "<who is most impacted by this bug>",
  "workaround": "<known workaround if any, else null>"
}}"""


# ------------------------------------------------------------------ #
# Exploration strategy — decides what to visit next
# ------------------------------------------------------------------ #

def exploration_strategy_prompt(
    visited_urls: list[str],
    discovered_links: list[dict],  # [{url, rationale, priority}]
    bugs_so_far: list[str],
    persona: dict,
    depth: int,
    max_depth: int,
    nav_graph_summary: dict,
) -> str:
    candidate_str = "\n".join(
        f"  [{l.get('priority',3)}] {l['url']} — {l.get('rationale','')}"
        if isinstance(l, dict) else f"  {l}"
        for l in (discovered_links or [])[:25]
    )
    return f"""PERSONA: {persona['name']}
EXPLORATION PRIORITY: {persona['exploration_priority']}

NAVIGATION GRAPH SUMMARY:
  Pages visited:   {nav_graph_summary.get('pages_reachable', len(visited_urls))}
  Total transitions: {nav_graph_summary.get('transitions', 0)}
  Blocked paths:   {nav_graph_summary.get('blocked', 0)}
  Most visited:    {nav_graph_summary.get('most_visited', [])}

CURRENT DEPTH: {depth}/{max_depth}
BUGS FOUND SO FAR ({len(bugs_so_far)}): {bugs_so_far[:5]}

CANDIDATE NEXT URLS (with AI-assigned priority):
{candidate_str or '  none'}

VISITED URLS ({len(visited_urls)}):
{chr(10).join(f'  {u}' for u in visited_urls[:15])}

TASK:
Decide the optimal next URLs to visit given the persona's exploration style and remaining depth budget.
Prefer unvisited high-value pages. Don't revisit. Stop early if coverage is adequate.

Output ONLY this JSON:
{{
  "reason_trace": "<why these URLs are chosen>",
  "next_urls": ["<url1>", "<url2>"],
  "should_stop": <true|false>,
  "stop_reason": "<if stopping: why>"
}}"""


# ------------------------------------------------------------------ #
# Element interaction refinement — recovers from failed selectors
# ------------------------------------------------------------------ #

def selector_recovery_prompt(
    failed_selector: str,
    action_type: str,
    dom_snapshot: str,
    description: str,
) -> str:
    return f"""A browser action failed because the CSS selector did not match.

Failed selector: {failed_selector}
Action type:     {action_type}
Intent:          {description}

Relevant DOM:
{dom_snapshot[:1500]}

Find the correct selector for this element. Output ONLY this JSON:
{{
  "reason_trace": "<what element you're targeting and why>",
  "selectors": [
    {{"selector": "<best CSS selector>", "confidence": <0.0-1.0>}},
    {{"selector": "<fallback selector>", "confidence": <0.0-1.0>}}
  ],
  "element_found": <true|false>,
  "alt_approach": "<if element not found, describe alternative action>"
}}"""


# ------------------------------------------------------------------ #
# Final report — executive summary
# ------------------------------------------------------------------ #

def final_report_prompt(session_summary: dict) -> str:
    return f"""You are a senior QA lead writing a final test report for an engineering team.

SESSION DATA:
{session_summary}

Write an executive summary that:
1. States the overall health clearly and honestly.
2. Highlights the 2-3 most impactful bugs.
3. Notes what was covered and what was NOT tested (gaps).
4. Gives prioritised, actionable recommendations — not generic advice.

Output ONLY this JSON:
{{
  "reason_trace": "<QA lead reasoning about the overall state>",
  "executive_summary": "<3-4 sentences — honest, specific, actionable>",
  "overall_health": "<healthy|degraded|broken|critical>",
  "critical_path_status": "<ok|at_risk|broken>",
  "top_recommendations": [
    "<specific action 1>",
    "<specific action 2>",
    "<specific action 3>"
  ],
  "test_coverage_assessment": "<what flows were tested and what gaps remain>",
  "ci_gate_recommendation": "<pass|warn|fail — should CI block on these results>"
}}"""


# ------------------------------------------------------------------ #
# Accessibility audit — dedicated pass per page
# ------------------------------------------------------------------ #

def accessibility_audit_prompt(url: str, dom_summary: str) -> str:
    return f"""You are an accessibility auditor checking WCAG 2.1 AA compliance.

PAGE URL: {url}

DOM SUMMARY:
{dom_summary}

Audit this page for accessibility issues. Output ONLY this JSON:
{{
  "issues": [
    {{
      "rule": "<WCAG rule, e.g. 1.1.1 Non-text Content>",
      "element": "<CSS selector>",
      "severity": "<low|medium|high|critical>",
      "description": "<what is wrong>",
      "fix": "<how to fix it>"
    }}
  ],
  "score": <0-100>,
  "summary": "<1 sentence>"
}}"""
