/**
 * Tests for BugReporter — rendering and dashboard building logic.
 */
import { v4 as uuidv4 } from 'uuid';
import { BugReporter, buildDashboard } from '../reporters/BugReporter';
import type { TestRun, TestScenario, BugReport, Dashboard } from '../types';

function makeRun(overrides: Partial<TestRun> = {}): TestRun {
  return {
    id: uuidv4(),
    url: 'https://demo.example.com',
    status: 'completed',
    startedAt: '2024-01-01T00:00:00.000Z',
    completedAt: '2024-01-01T00:01:00.000Z',
    totalScenarios: 3,
    passedScenarios: 2,
    failedScenarios: 1,
    bugsFound: 1,
    ...overrides,
  };
}

function makeBug(runId: string, overrides: Partial<BugReport> = {}): BugReport {
  return {
    id: uuidv4(),
    runId,
    title: 'Login button unresponsive',
    severity: 'high',
    description: 'Clicking the login button does not trigger any action.',
    reproductionSteps: ['Navigate to /login', 'Enter valid credentials', 'Click Login'],
    expectedBehavior: 'User is redirected to dashboard.',
    actualBehavior: 'Nothing happens.',
    url: 'https://demo.example.com/login',
    detectedAt: '2024-01-01T00:00:30.000Z',
    ...overrides,
  };
}

function makeScenario(runId: string, overrides: Partial<TestScenario> = {}): TestScenario {
  return {
    id: uuidv4(),
    runId,
    title: 'Login flow',
    description: 'Test user login',
    priority: 'high',
    steps: [],
    status: 'passed',
    ...overrides,
  };
}

describe('BugReporter.renderBug', () => {
  test('includes all key fields in the output', () => {
    const run = makeRun();
    const bug = makeBug(run.id, { screenshotPath: '/screenshots/bug1.png' });
    const output = BugReporter.renderBug(bug);

    expect(output).toContain(bug.title);
    expect(output).toContain('HIGH');
    expect(output).toContain(bug.url);
    expect(output).toContain(bug.description);
    expect(output).toContain('Navigate to /login');
    expect(output).toContain(bug.expectedBehavior);
    expect(output).toContain(bug.actualBehavior);
    expect(output).toContain('/screenshots/bug1.png');
  });

  test('renders console errors when present', () => {
    const run = makeRun();
    const bug = makeBug(run.id, {
      consoleErrors: ['Uncaught TypeError: null is not an object'],
    });
    const output = BugReporter.renderBug(bug);
    expect(output).toContain('Console Errors');
    expect(output).toContain('Uncaught TypeError');
  });

  test('renders error stack when present', () => {
    const run = makeRun();
    const bug = makeBug(run.id, { errorStack: 'Error: test\n  at login (app.js:42)' });
    const output = BugReporter.renderBug(bug);
    expect(output).toContain('Error Stack');
    expect(output).toContain('app.js:42');
  });

  test('omits screenshot section when path is absent', () => {
    const run = makeRun();
    const bug = makeBug(run.id); // no screenshotPath
    const output = BugReporter.renderBug(bug);
    expect(output).not.toContain('**Screenshot:**');
  });
});

describe('buildDashboard', () => {
  test('builds correct severity counts', () => {
    const run = makeRun({ bugsFound: 3 });
    const bugs: BugReport[] = [
      makeBug(run.id, { severity: 'critical' }),
      makeBug(run.id, { severity: 'high' }),
      makeBug(run.id, { severity: 'medium' }),
    ];
    const scenarios = [makeScenario(run.id)];
    const dashboard = buildDashboard(run, scenarios, bugs);

    expect(dashboard.severityCounts.critical).toBe(1);
    expect(dashboard.severityCounts.high).toBe(1);
    expect(dashboard.severityCounts.medium).toBe(1);
    expect(dashboard.severityCounts.low).toBe(0);
  });

  test('calculates duration from timestamps', () => {
    const run = makeRun({
      startedAt: '2024-01-01T00:00:00.000Z',
      completedAt: '2024-01-01T00:01:00.000Z', // 60 seconds
    });
    const dashboard = buildDashboard(run, [], []);
    expect(dashboard.durationMs).toBe(60_000);
  });

  test('infers coverage areas from scenario titles', () => {
    const run = makeRun();
    const scenarios: TestScenario[] = [
      makeScenario(run.id, { title: 'Login flow' }),
      makeScenario(run.id, { title: 'Signup with email' }),
      makeScenario(run.id, { title: 'Navigation menu interaction' }),
      makeScenario(run.id, { title: 'Search for product' }),
    ];
    const dashboard = buildDashboard(run, scenarios, []);
    expect(dashboard.coverageAreas).toContain('Authentication — Login');
    expect(dashboard.coverageAreas).toContain('Authentication — Signup');
    expect(dashboard.coverageAreas).toContain('Navigation');
    expect(dashboard.coverageAreas).toContain('Search');
  });

  test('counts skipped scenarios', () => {
    const run = makeRun({ totalScenarios: 3 });
    const scenarios: TestScenario[] = [
      makeScenario(run.id, { status: 'passed' }),
      makeScenario(run.id, { status: 'failed' }),
      makeScenario(run.id, { status: 'skipped' }),
    ];
    const dashboard = buildDashboard(run, scenarios, []);
    expect(dashboard.skippedScenarios).toBe(1);
  });
});

describe('BugReporter.renderDashboard', () => {
  test('renders a valid Markdown report', () => {
    const run = makeRun();
    const bug = makeBug(run.id);
    const scenarios = [makeScenario(run.id, { status: 'passed' })];
    const dashboard: Dashboard = buildDashboard(run, scenarios, [bug]);

    const output = BugReporter.renderDashboard(dashboard);
    expect(output).toContain('QA Copilot');
    expect(output).toContain(run.id);
    expect(output).toContain(run.url);
    expect(output).toContain('Login button unresponsive');
    expect(output).toContain('Login flow');
  });

  test('shows "No bugs detected" when there are no bugs', () => {
    const run = makeRun({ bugsFound: 0, failedScenarios: 0 });
    const scenarios = [makeScenario(run.id)];
    const dashboard: Dashboard = buildDashboard(run, scenarios, []);

    const output = BugReporter.renderDashboard(dashboard);
    expect(output).toContain('No bugs detected');
  });
});
