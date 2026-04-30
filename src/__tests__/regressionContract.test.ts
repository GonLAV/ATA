import { RegressionContractBuilder } from '../contracts/RegressionContractBuilder';
import type { BugReport, ProductRiskSignal, TestRun, TestScenario } from '../types';

const run: TestRun = {
  id: 'run-123',
  url: 'https://example.com',
  status: 'completed',
  startedAt: '2026-04-30T00:00:00.000Z',
  completedAt: '2026-04-30T00:00:10.000Z',
  totalScenarios: 1,
  passedScenarios: 0,
  failedScenarios: 1,
  bugsFound: 1,
};

const scenario: TestScenario = {
  id: 'scenario-1',
  runId: run.id,
  title: 'Submit signup form',
  description: 'Exercises signup form',
  priority: 'high',
  status: 'failed',
  steps: [
    { action: 'navigate', value: 'https://example.com/signup', description: 'Open signup' },
    { action: 'fill', selector: '#email', value: 'qa@example.com', description: 'Fill email' },
    { action: 'click', selector: 'button:has-text("Sign up")', description: 'Submit signup' },
    { action: 'assert_url', value: '/dashboard', description: 'Expect dashboard' },
  ],
};

const bug: BugReport = {
  id: 'bug-1',
  runId: run.id,
  scenarioId: scenario.id,
  title: 'Signup does not redirect',
  severity: 'high',
  description: 'Signup submission stays on the same page.',
  reproductionSteps: ['Open signup', 'Fill email', 'Submit signup'],
  expectedBehavior: 'User lands on dashboard.',
  actualBehavior: 'User remains on signup.',
  url: 'https://example.com/signup',
  detectedAt: '2026-04-30T00:00:05.000Z',
};

const risk: ProductRiskSignal = {
  id: 'risk-1',
  runId: run.id,
  scenarioId: scenario.id,
  type: 'dead_interaction',
  title: 'Primary signup CTA has no visible effect',
  severity: 'high',
  evidence: ['URL did not change', 'DOM did not change'],
  recommendation: 'Add visible feedback or fix the handler.',
  url: 'https://example.com/signup',
  detectedAt: '2026-04-30T00:00:06.000Z',
};

describe('RegressionContractBuilder', () => {
  test('generates Playwright specs from bugs and risk signals', () => {
    const contract = new RegressionContractBuilder().build(run, [scenario], [bug], [risk]);

    expect(contract.framework).toBe('playwright');
    expect(contract.testCount).toBe(2);
    expect(contract.sourceIssueCount).toBe(1);
    expect(contract.sourceRiskCount).toBe(1);
    expect(contract.specFilename).toBe('qa-copilot-run-123.spec.ts');
    expect(contract.spec).toContain("import { test, expect } from '@playwright/test'");
    expect(contract.spec).toContain('Signup does not redirect');
    expect(contract.spec).toContain('Primary signup CTA has no visible effect');
    expect(contract.spec).toContain('await page.locator("#email").fill("qa@example.com");');
    expect(contract.spec).toContain('await expect(page).toHaveURL(new RegExp("/dashboard"));');
  });

  test('generates a baseline smoke contract when there are no findings', () => {
    const contract = new RegressionContractBuilder().build(run, [], [], []);

    expect(contract.testCount).toBe(1);
    expect(contract.spec).toContain('baseline page remains reachable');
    expect(contract.spec).toContain('await expect(page.locator(\'body\')).toBeVisible();');
  });
});