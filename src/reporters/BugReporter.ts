import type { BugReport, Dashboard, TestRun, TestScenario } from '../types';

/**
 * Generates human-readable bug reports and a summary dashboard.
 */
export class BugReporter {
  /**
   * Render a single bug report as a Markdown string.
   */
  static renderBug(bug: BugReport): string {
    const steps = bug.reproductionSteps
      .map((s, i) => `${i + 1}. ${s}`)
      .join('\n');

    const screenshot = bug.screenshotPath
      ? `\n**Screenshot:** \`${bug.screenshotPath}\``
      : '';

    const consoleSection =
      bug.consoleErrors && bug.consoleErrors.length > 0
        ? `\n\n**Console Errors:**\n${bug.consoleErrors.map((e) => `- \`${e}\``).join('\n')}`
        : '';

    const networkSection =
      bug.networkErrors && bug.networkErrors.length > 0
        ? `\n\n**Network Errors:**\n${bug.networkErrors.map((e) => `- \`${e}\``).join('\n')}`
        : '';

    const stackSection = bug.errorStack
      ? `\n\n**Error Stack:**\n\`\`\`\n${bug.errorStack}\n\`\`\``
      : '';

    return `## 🐛 ${bug.title}

| Field | Value |
|-------|-------|
| **ID** | \`${bug.id}\` |
| **Severity** | ${severityEmoji(bug.severity)} ${bug.severity.toUpperCase()} |
| **URL** | ${bug.url} |
| **Detected** | ${bug.detectedAt} |

### Description
${bug.description}

### Reproduction Steps
${steps}

### Expected Behavior
${bug.expectedBehavior}

### Actual Behavior
${bug.actualBehavior}${screenshot}${consoleSection}${networkSection}${stackSection}
`;
  }

  /**
   * Render the full dashboard as a Markdown document.
   */
  static renderDashboard(dashboard: Dashboard): string {
    const duration = dashboard.durationMs
      ? `${(dashboard.durationMs / 1000).toFixed(1)}s`
      : 'N/A';

    const bugSection =
      dashboard.bugs.length > 0
        ? dashboard.bugs.map(BugReporter.renderBug).join('\n---\n')
        : '_No bugs detected._';

    const scenarioRows = dashboard.scenarios
      .map(
        (s) =>
          `| ${statusEmoji(s.status)} | ${s.title} | ${s.priority} | ${s.durationMs != null ? `${s.durationMs}ms` : '—'} |`,
      )
      .join('\n');

    const coverageList =
      dashboard.coverageAreas.length > 0
        ? dashboard.coverageAreas.map((a) => `- ${a}`).join('\n')
        : '- N/A';

    return `# 📊 QA Copilot — Test Run Report

| Field | Value |
|-------|-------|
| **Run ID** | \`${dashboard.runId}\` |
| **URL** | ${dashboard.url} |
| **Status** | ${dashboard.status} |
| **Started** | ${dashboard.startedAt} |
| **Completed** | ${dashboard.completedAt ?? 'N/A'} |
| **Duration** | ${duration} |

---

## Summary

| Metric | Count |
|--------|-------|
| Total Scenarios | ${dashboard.totalScenarios} |
| ✅ Passed | ${dashboard.passedScenarios} |
| ❌ Failed | ${dashboard.failedScenarios} |
| ⏭️ Skipped | ${dashboard.skippedScenarios} |
| 🐛 Bugs Found | ${dashboard.bugsFound} |

### Bug Severity Breakdown

| Severity | Count |
|----------|-------|
| 🔴 Critical | ${dashboard.severityCounts.critical} |
| 🟠 High | ${dashboard.severityCounts.high} |
| 🟡 Medium | ${dashboard.severityCounts.medium} |
| 🟢 Low | ${dashboard.severityCounts.low} |

---

## Coverage Areas
${coverageList}

---

## Test Scenarios

| Status | Title | Priority | Duration |
|--------|-------|----------|----------|
${scenarioRows}

---

## Bug Reports

${bugSection}
`;
  }
}

function severityEmoji(severity: string): string {
  switch (severity) {
    case 'critical': return '🔴';
    case 'high': return '🟠';
    case 'medium': return '🟡';
    case 'low': return '🟢';
    default: return '⚪';
  }
}

function statusEmoji(status: string): string {
  switch (status) {
    case 'passed': return '✅';
    case 'failed': return '❌';
    case 'skipped': return '⏭️';
    default: return '⏳';
  }
}

/**
 * Build a Dashboard object from a run, its scenarios, and its bugs.
 */
export function buildDashboard(
  run: TestRun,
  scenarios: TestScenario[],
  bugs: BugReport[],
): Dashboard {
  const severityCounts = { critical: 0, high: 0, medium: 0, low: 0 };
  for (const bug of bugs) {
    severityCounts[bug.severity] = (severityCounts[bug.severity] ?? 0) + 1;
  }

  const skipped = scenarios.filter((s) => s.status === 'skipped').length;

  const durationMs =
    run.startedAt && run.completedAt
      ? new Date(run.completedAt).getTime() - new Date(run.startedAt).getTime()
      : undefined;

  const coverageAreas = inferCoverageAreas(scenarios);

  return {
    runId: run.id,
    url: run.url,
    status: run.status,
    startedAt: run.startedAt,
    completedAt: run.completedAt,
    durationMs,
    totalScenarios: run.totalScenarios,
    passedScenarios: run.passedScenarios,
    failedScenarios: run.failedScenarios,
    skippedScenarios: skipped,
    bugsFound: run.bugsFound,
    severityCounts,
    bugs,
    scenarios,
    coverageAreas,
  };
}

function inferCoverageAreas(scenarios: TestScenario[]): string[] {
  const areas = new Set<string>();
  for (const s of scenarios) {
    const title = s.title.toLowerCase();
    if (title.includes('login') || title.includes('sign in')) areas.add('Authentication — Login');
    if (title.includes('signup') || title.includes('register')) areas.add('Authentication — Signup');
    if (title.includes('nav') || title.includes('menu')) areas.add('Navigation');
    if (title.includes('form')) areas.add('Form Submission');
    if (title.includes('button')) areas.add('Button Interactions');
    if (title.includes('error') || title.includes('invalid')) areas.add('Error Handling');
    if (title.includes('search')) areas.add('Search');
    if (title.includes('link')) areas.add('Link Navigation');
  }
  return Array.from(areas);
}
