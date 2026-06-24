"""
CI/CD Integration — QA Copilot output adapters.

Exports session results in formats consumed by CI pipelines:
  • JUnit XML  → GitHub Actions, Jenkins, CircleCI test reports
  • JSON badge → Shields.io dynamic badge (health, bug count)
  • SARIF       → GitHub Code Scanning / Security tab
  • Summary MD  → GitHub Actions job summary ($GITHUB_STEP_SUMMARY)

Usage (CLI):
  python -m cicd.exporter --session <id> --format junit --out results.xml
  python -m cicd.exporter --session <id> --format sarif --out results.sarif.json
  python -m cicd.exporter --session <id> --format badge
  python -m cicd.exporter --session <id> --format summary

Exit codes (for CI gate):
  0 → pass (healthy / low severity only)
  1 → warn (medium severity bugs present)
  2 → fail (high or critical bugs present)
"""
from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any


# ------------------------------------------------------------------ #
# JUnit XML
# ------------------------------------------------------------------ #

def to_junit_xml(report: dict[str, Any]) -> str:
    """Convert a QA Copilot report to JUnit XML."""
    bugs: list[dict] = report.get("bugs", [])
    stats: dict = report.get("stats", {})
    url: str = report.get("target_url", "unknown")
    duration: float = report.get("duration_seconds", 0.0)

    suite = ET.Element(
        "testsuite",
        name=f"QA Copilot — {url}",
        tests=str(max(len(bugs), 1)),
        failures=str(sum(1 for b in bugs if b.get("severity") in ("high", "critical"))),
        errors=str(sum(1 for b in bugs if b.get("severity") == "critical")),
        time=str(round(duration, 2)),
        timestamp=datetime.utcnow().isoformat(),
    )

    if not bugs:
        # At least one passing test case so CI doesn't cry about empty suites
        tc = ET.SubElement(
            suite, "testcase",
            name="QA Copilot — No bugs found",
            classname="QACopilot",
            time="0",
        )
        ET.SubElement(tc, "system-out").text = (
            f"Explored {stats.get('pages_visited', 0)} pages. Health: {report.get('overall_health', 'healthy')}."
        )
    else:
        for bug in bugs:
            sev = bug.get("severity", "low")
            classname = f"QACopilot.{bug.get('error_type', 'issue')}"
            tc = ET.SubElement(
                suite,
                "testcase",
                name=bug.get("title", "Unnamed bug"),
                classname=classname,
                time="0",
            )
            steps = "\n".join(
                f"{i+1}. {s}" for i, s in enumerate(bug.get("reproduction_steps", []))
            )
            detail = (
                f"Severity: {sev.upper()}\n"
                f"Persona: {bug.get('persona_name', '—')}\n"
                f"URL: {bug.get('url_at_error', '—')}\n\n"
                f"Expected: {bug.get('expected_behavior', '')}\n"
                f"Actual:   {bug.get('actual_behavior', '')}\n\n"
                f"Steps:\n{steps}"
            )
            if sev in ("critical", "high"):
                ET.SubElement(tc, "failure", message=bug.get("title", ""), type=sev).text = detail
            elif sev == "medium":
                ET.SubElement(tc, "error", message=bug.get("title", ""), type=sev).text = detail
            else:
                ET.SubElement(tc, "system-out").text = detail

    tree = ET.ElementTree(suite)
    ET.indent(tree, space="  ")
    import io
    buf = io.BytesIO()
    tree.write(buf, encoding="utf-8", xml_declaration=True)
    return buf.getvalue().decode()


# ------------------------------------------------------------------ #
# JSON Badge (Shields.io endpoint format)
# ------------------------------------------------------------------ #

def to_badge_json(report: dict[str, Any]) -> dict:
    health = report.get("overall_health", "unknown")
    bugs = report.get("bugs", [])
    critical = sum(1 for b in bugs if b.get("severity") == "critical")
    high = sum(1 for b in bugs if b.get("severity") == "high")

    colour_map = {
        "healthy": "brightgreen",
        "degraded": "yellow",
        "broken": "orange",
        "critical": "red",
    }
    label_map = {
        "healthy": f"✓ healthy",
        "degraded": f"⚠ {len(bugs)} bugs",
        "broken": f"✗ {high}H {critical}C bugs",
        "critical": f"✗✗ {critical} critical",
    }
    return {
        "schemaVersion": 1,
        "label": "QA Copilot",
        "message": label_map.get(health, health),
        "color": colour_map.get(health, "lightgrey"),
        "namedLogo": "playwright",
    }


# ------------------------------------------------------------------ #
# SARIF (GitHub Code Scanning)
# ------------------------------------------------------------------ #

def to_sarif(report: dict[str, Any]) -> dict:
    bugs: list[dict] = report.get("bugs", [])
    level_map = {"critical": "error", "high": "error", "medium": "warning", "low": "note"}

    results = [
        {
            "ruleId": f"QA-{bug.get('error_type', 'issue').upper().replace('_', '-')}",
            "level": level_map.get(bug.get("severity", "low"), "note"),
            "message": {
                "text": (
                    f"{bug.get('title','')}\n\n"
                    f"Expected: {bug.get('expected_behavior','')}\n"
                    f"Actual:   {bug.get('actual_behavior','')}\n"
                    f"Persona:  {bug.get('persona_name','')}"
                )
            },
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": bug.get("url_at_error", report.get("target_url", ""))}
                    }
                }
            ],
            "fingerprints": {"primary/v1": bug.get("title", "")[:64]},
        }
        for bug in bugs
    ]

    rules = list({
        bug.get("error_type", "issue")
        for bug in bugs
    })

    return {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "QA Copilot",
                        "version": "1.0.0",
                        "rules": [
                            {
                                "id": f"QA-{r.upper().replace('_', '-')}",
                                "name": r.replace("_", " ").title(),
                                "shortDescription": {"text": f"QA Copilot detected: {r}"},
                            }
                            for r in rules
                        ],
                    }
                },
                "results": results,
            }
        ],
    }


# ------------------------------------------------------------------ #
# GitHub Actions Markdown Summary
# ------------------------------------------------------------------ #

def to_github_summary(report: dict[str, Any]) -> str:
    bugs: list[dict] = report.get("bugs", [])
    stats = report.get("stats", {})
    score = report.get("score", {})
    health = report.get("overall_health", "unknown")
    health_emoji = {"healthy": "✅", "degraded": "⚠️", "broken": "🔶", "critical": "🔴"}.get(health, "❓")

    sev_counts = score.get("by_severity", {})
    lines = [
        f"## {health_emoji} QA Copilot Report — {report.get('target_url', '')}",
        "",
        f"| Metric | Value |",
        f"| --- | --- |",
        f"| Overall Health | **{health.upper()}** |",
        f"| Total Bugs | {len(bugs)} |",
        f"| Critical | 🔴 {sev_counts.get('critical', 0)} |",
        f"| High | 🟠 {sev_counts.get('high', 0)} |",
        f"| Medium | 🟡 {sev_counts.get('medium', 0)} |",
        f"| Low | 🟢 {sev_counts.get('low', 0)} |",
        f"| Pages Visited | {stats.get('pages_visited', 0)} |",
        f"| Risk Score | {score.get('total_score', 0)} |",
        f"| Duration | {report.get('duration_seconds', 0):.0f}s |",
        "",
        "### Executive Summary",
        report.get("executive_summary", "_No summary available._"),
        "",
    ]

    if report.get("top_recommendations"):
        lines += ["### Recommendations", ""]
        for rec in report["top_recommendations"]:
            lines.append(f"- {rec}")
        lines.append("")

    if bugs:
        critical_high = [b for b in bugs if b.get("severity") in ("critical", "high")]
        if critical_high:
            lines += ["### Critical & High Bugs", ""]
            for b in critical_high[:10]:
                sev_emoji = "🔴" if b.get("severity") == "critical" else "🟠"
                lines.append(f"#### {sev_emoji} {b.get('title', '')}")
                lines.append(f"- **URL:** {b.get('url_at_error', '—')}")
                lines.append(f"- **Persona:** {b.get('persona_name', '—')}")
                lines.append(f"- **Expected:** {b.get('expected_behavior', '')}")
                lines.append(f"- **Actual:** {b.get('actual_behavior', '')}")
                if b.get("reproduction_steps"):
                    lines.append(f"- **Steps:** " + " → ".join(str(s) for s in b["reproduction_steps"][:4]))
                lines.append("")

    lines += [
        "---",
        f"_Generated by QA Copilot at {report.get('completed_at', datetime.utcnow().isoformat())}_",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------ #
# Exit code
# ------------------------------------------------------------------ #

def ci_exit_code(report: dict[str, Any]) -> int:
    health = report.get("overall_health", "healthy")
    gate = report.get("ci_gate_recommendation", "")
    if gate == "fail" or health == "critical":
        return 2
    if gate == "warn" or health in ("broken", "degraded"):
        return 1
    return 0


# ------------------------------------------------------------------ #
# CLI entry point
# ------------------------------------------------------------------ #

async def _fetch_report(session_id: str, base_url: str = "http://localhost:8000") -> dict:
    import httpx
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{base_url}/api/sessions/{session_id}/report")
        r.raise_for_status()
        return r.json()


def main() -> None:
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="QA Copilot CI/CD exporter")
    parser.add_argument("--session", required=True, help="Session ID")
    parser.add_argument(
        "--format",
        choices=["junit", "badge", "sarif", "summary"],
        default="junit",
    )
    parser.add_argument("--out", help="Output file (stdout if omitted)")
    parser.add_argument("--api", default="http://localhost:8000", help="QA Copilot API base URL")
    parser.add_argument("--fail-on", choices=["high", "medium", "any"], default="high",
                        help="Exit non-zero when bugs of this severity or above exist")
    args = parser.parse_args()

    report = asyncio.run(_fetch_report(args.session, args.api))

    if args.format == "junit":
        output = to_junit_xml(report)
    elif args.format == "badge":
        output = json.dumps(to_badge_json(report), indent=2)
    elif args.format == "sarif":
        output = json.dumps(to_sarif(report), indent=2)
    elif args.format == "summary":
        output = to_github_summary(report)
    else:
        output = json.dumps(report, indent=2)

    if args.out:
        Path(args.out).write_text(output)
        print(f"Written to {args.out}", file=sys.stderr)
    else:
        print(output)

    sys.exit(ci_exit_code(report))


if __name__ == "__main__":
    main()
