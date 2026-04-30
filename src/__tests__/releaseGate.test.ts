import { ReleaseGateEvaluator } from '../gates/ReleaseGateEvaluator';
import type { BugReport, ProductRiskSignal, TestRun, TestScenario } from '../types';

function makeRun(overrides: Partial<TestRun> = {}): TestRun {
  return {
    id: 'run-1',
    url: 'https://example.com',
    status: 'completed',
    startedAt: '2026-04-30T00:00:00.000Z',
    completedAt: '2026-04-30T00:00:30.000Z',
    totalScenarios: 2,
    passedScenarios: 2,
    failedScenarios: 0,
    bugsFound: 0,
    ...overrides,
  };
}

function makeScenario(overrides: Partial<TestScenario> = {}): TestScenario {
  return {
    id: 'scenario-1',
    runId: 'run-1',
    title: 'Critical user journey',
    description: 'Exercises the key flow',
    priority: 'high',
    steps: [],
    status: 'passed',
    ...overrides,
  };
}

function makeBug(overrides: Partial<BugReport> = {}): BugReport {
  return {
    id: 'bug-1',
    runId: 'run-1',
    title: 'Checkout fails',
    severity: 'high',
    description: 'Checkout cannot complete.',
    reproductionSteps: ['Open cart', 'Click checkout'],
    expectedBehavior: 'Checkout completes.',
    actualBehavior: 'Checkout stays blocked.',
    url: 'https://example.com/cart',
    detectedAt: '2026-04-30T00:00:10.000Z',
    ...overrides,
  };
}

function makeRisk(overrides: Partial<ProductRiskSignal> = {}): ProductRiskSignal {
  return {
    id: 'risk-1',
    runId: 'run-1',
    type: 'dead_interaction',
    title: 'CTA has no visible response',
    severity: 'medium',
    evidence: ['No DOM change'],
    recommendation: 'Add visible feedback.',
    url: 'https://example.com',
    detectedAt: '2026-04-30T00:00:11.000Z',
    ...overrides,
  };
}

describe('ReleaseGateEvaluator', () => {
  test('ships when all checks pass', () => {
    const gate = new ReleaseGateEvaluator().evaluate(
      makeRun(),
      [makeScenario(), makeScenario({ id: 'scenario-2' })],
      [],
      [],
    );

    expect(gate.decision).toBe('ship');
    expect(gate.ciExitCode).toBe(0);
    expect(gate.summary).toContain('clear to ship');
  });

  test('blocks when high severity bugs are present', () => {
    const gate = new ReleaseGateEvaluator().evaluate(
      makeRun({ bugsFound: 1, failedScenarios: 1, passedScenarios: 1 }),
      [makeScenario(), makeScenario({ id: 'scenario-2', status: 'failed' })],
      [makeBug()],
      [],
    );

    expect(gate.decision).toBe('block');
    expect(gate.ciExitCode).toBe(1);
    expect(gate.requiredActions).toContain('Fix bug: Checkout fails');
  });

  test('warns for high product risk within non-blocking thresholds', () => {
    const gate = new ReleaseGateEvaluator({ maxHighSeverityRisks: 0, minRiskScore: 70 }).evaluate(
      makeRun(),
      [makeScenario(), makeScenario({ id: 'scenario-2' })],
      [],
      [makeRisk({ severity: 'high' })],
    );

    expect(gate.decision).toBe('warn');
    expect(gate.ciExitCode).toBe(0);
    expect(gate.requiredActions).toContain('Review risk: CTA has no visible response');
  });
});