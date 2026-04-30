import type { BugReport, ProductRiskSignal, RegressionContract, TestRun, TestScenario, TestStep } from '../types';

interface SourceFinding {
  id: string;
  title: string;
  severity: string;
  url: string;
  scenario?: TestScenario;
  notes: string[];
}

/**
 * Converts autonomous QA findings into an executable Playwright spec. This is
 * the handoff layer from AI exploration to durable CI coverage.
 */
export class RegressionContractBuilder {
  build(run: TestRun, scenarios: TestScenario[], bugs: BugReport[], risks: ProductRiskSignal[]): RegressionContract {
    const scenarioById = new Map(scenarios.map((scenario) => [scenario.id, scenario]));
    const findings = [
      ...bugs.map((bug): SourceFinding => ({
        id: bug.id,
        title: bug.title,
        severity: bug.severity,
        url: bug.url,
        scenario: bug.scenarioId ? scenarioById.get(bug.scenarioId) : undefined,
        notes: [
          `Expected: ${bug.expectedBehavior}`,
          `Actual: ${bug.actualBehavior}`,
          ...bug.reproductionSteps.map((step, index) => `Step ${index + 1}: ${step}`),
        ],
      })),
      ...risks.map((risk): SourceFinding => ({
        id: risk.id,
        title: risk.title,
        severity: risk.severity,
        url: risk.url,
        scenario: risk.scenarioId ? scenarioById.get(risk.scenarioId) : undefined,
        notes: [
          `Risk type: ${risk.type}`,
          `Recommendation: ${risk.recommendation}`,
          ...risk.evidence.map((item) => `Evidence: ${item}`),
        ],
      })),
    ];

    const specFilename = `qa-copilot-${safeName(run.id)}.spec.ts`;
    const spec = this.renderSpec(run, findings);

    return {
      runId: run.id,
      url: run.url,
      generatedAt: new Date().toISOString(),
      framework: 'playwright',
      testCount: Math.max(findings.length, 1),
      sourceIssueCount: bugs.length,
      sourceRiskCount: risks.length,
      specFilename,
      spec,
    };
  }

  private renderSpec(run: TestRun, findings: SourceFinding[]): string {
    const tests = findings.length > 0
      ? findings.map((finding, index) => this.renderFindingTest(finding, index + 1)).join('\n\n')
      : this.renderSmokeTest(run);

    return `import { test, expect } from '@playwright/test';

test.describe('QA Copilot regression contract: ${escapeComment(run.url)}', () => {
${indent(tests, 2)}
});
`;
  }

  private renderFindingTest(finding: SourceFinding, index: number): string {
    const steps = finding.scenario?.steps.length
      ? finding.scenario.steps.map((step) => this.renderStep(step)).filter(Boolean).join('\n')
      : `await page.goto(${quote(finding.url)}, { waitUntil: 'domcontentloaded' });\nawait expect(page).toHaveURL(/.+/);`;

    const notes = finding.notes.map((note) => `// ${escapeComment(note)}`).join('\n');

    return `test(${quote(`${index}. ${finding.severity.toUpperCase()} - ${finding.title}`)}, async ({ page }) => {
${indent(notes, 2)}
${indent(steps, 2)}
});`;
  }

  private renderSmokeTest(run: TestRun): string {
    return `test('1. baseline page remains reachable', async ({ page }) => {
  await page.goto(${quote(run.url)}, { waitUntil: 'domcontentloaded' });
  await expect(page.locator('body')).toBeVisible();
});`;
  }

  private renderStep(step: TestStep): string {
    switch (step.action) {
      case 'navigate':
        return `await page.goto(${quote(step.value ?? '/')}, { waitUntil: 'domcontentloaded' });`;
      case 'click':
      case 'submit':
        return step.selector ? `await page.locator(${quote(step.selector)}).click();` : '';
      case 'fill':
        return step.selector ? `await page.locator(${quote(step.selector)}).fill(${quote(step.value ?? '')});` : '';
      case 'wait':
        return `await page.waitForTimeout(${Number.parseInt(step.value ?? '1000', 10)});`;
      case 'screenshot':
        return `await page.screenshot({ path: ${quote(`regression-${safeName(step.value ?? 'step')}.png`)}, fullPage: true });`;
      case 'assert_visible':
        return step.selector ? `await expect(page.locator(${quote(step.selector)})).toBeVisible();` : '';
      case 'assert_text':
        return step.selector ? `await expect(page.locator(${quote(step.selector)})).toContainText(${quote(step.value ?? '')});` : '';
      case 'assert_url':
        return step.value ? `await expect(page).toHaveURL(new RegExp(${quote(escapeRegex(step.value))}));` : '';
      default:
        return '';
    }
  }
}

function quote(value: string): string {
  return JSON.stringify(value);
}

function indent(value: string, spaces: number): string {
  const padding = ' '.repeat(spaces);
  return value.split('\n').map((line) => line ? `${padding}${line}` : line).join('\n');
}

function safeName(value: string): string {
  return value.replace(/[^a-z0-9_-]/gi, '_').slice(0, 80);
}

function escapeComment(value: string): string {
  return value.replace(/\r?\n/g, ' ').replace(/\*\//g, '* /');
}

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}