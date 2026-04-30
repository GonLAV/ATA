/**
 * Tests for Database layer (Repository) using an in-memory SQLite instance.
 */
import { v4 as uuidv4 } from 'uuid';

// Use an in-memory database for tests
process.env.DATABASE_PATH = ':memory:';

import { closeDatabase } from '../database/Database';
import {
  createRun,
  updateRun,
  getRun,
  getAllRuns,
  createScenario,
  updateScenario,
  getScenariosByRun,
  createBugReport,
  getBugsByRun,
} from '../database/Repository';
import type { TestRun, TestScenario, BugReport } from '../types';

function makeRun(overrides: Partial<TestRun> = {}): TestRun {
  return {
    id: uuidv4(),
    url: 'https://example.com',
    status: 'pending',
    startedAt: new Date().toISOString(),
    totalScenarios: 0,
    passedScenarios: 0,
    failedScenarios: 0,
    bugsFound: 0,
    ...overrides,
  };
}

function makeScenario(runId: string, overrides: Partial<TestScenario> = {}): TestScenario {
  return {
    id: uuidv4(),
    runId,
    title: 'Test login',
    description: 'Verify login flow',
    priority: 'high',
    steps: [{ action: 'navigate', value: 'https://example.com', description: 'Go to homepage' }],
    status: 'pending',
    ...overrides,
  };
}

function makeBug(runId: string, overrides: Partial<BugReport> = {}): BugReport {
  return {
    id: uuidv4(),
    runId,
    title: 'Button not clickable',
    severity: 'high',
    description: 'The submit button does not respond.',
    reproductionSteps: ['Open page', 'Click submit button'],
    expectedBehavior: 'Form submits',
    actualBehavior: 'Nothing happens',
    url: 'https://example.com',
    detectedAt: new Date().toISOString(),
    ...overrides,
  };
}

afterAll(() => {
  closeDatabase();
});

describe('Repository — Test Runs', () => {
  test('creates and retrieves a run', () => {
    const run = makeRun();
    createRun(run);

    const found = getRun(run.id);
    expect(found).toBeDefined();
    expect(found?.id).toBe(run.id);
    expect(found?.url).toBe(run.url);
    expect(found?.status).toBe('pending');
  });

  test('updates a run status', () => {
    const run = makeRun();
    createRun(run);

    updateRun(run.id, { status: 'completed', bugsFound: 3 });

    const updated = getRun(run.id);
    expect(updated?.status).toBe('completed');
    expect(updated?.bugsFound).toBe(3);
  });

  test('getAllRuns returns all persisted runs', () => {
    const run1 = makeRun();
    const run2 = makeRun();
    createRun(run1);
    createRun(run2);

    const all = getAllRuns();
    const ids = all.map((r) => r.id);
    expect(ids).toContain(run1.id);
    expect(ids).toContain(run2.id);
  });

  test('returns undefined for a missing run', () => {
    const result = getRun('non-existent-id');
    expect(result).toBeUndefined();
  });
});

describe('Repository — Test Scenarios', () => {
  test('creates and retrieves scenarios by run', () => {
    const run = makeRun();
    createRun(run);

    const s1 = makeScenario(run.id, { title: 'Login flow' });
    const s2 = makeScenario(run.id, { title: 'Signup flow' });
    createScenario(s1);
    createScenario(s2);

    const found = getScenariosByRun(run.id);
    expect(found).toHaveLength(2);
    expect(found.map((s) => s.title)).toContain('Login flow');
  });

  test('updates scenario status and duration', () => {
    const run = makeRun();
    createRun(run);

    const scenario = makeScenario(run.id);
    createScenario(scenario);

    updateScenario(scenario.id, { status: 'passed', durationMs: 1234 });

    const found = getScenariosByRun(run.id);
    const updated = found.find((s) => s.id === scenario.id);
    expect(updated?.status).toBe('passed');
    expect(updated?.durationMs).toBe(1234);
  });

  test('serialises and deserialises steps correctly', () => {
    const run = makeRun();
    createRun(run);

    const scenario = makeScenario(run.id, {
      steps: [
        { action: 'navigate', value: 'https://example.com', description: 'Open site' },
        { action: 'click', selector: '#btn', description: 'Click button' },
      ],
    });
    createScenario(scenario);

    const found = getScenariosByRun(run.id).find((s) => s.id === scenario.id);
    expect(found?.steps).toHaveLength(2);
    expect(found?.steps[1].action).toBe('click');
  });
});

describe('Repository — Bug Reports', () => {
  test('creates and retrieves bugs by run', () => {
    const run = makeRun();
    createRun(run);

    const bug = makeBug(run.id);
    createBugReport(bug);

    const found = getBugsByRun(run.id);
    expect(found).toHaveLength(1);
    expect(found[0].title).toBe(bug.title);
    expect(found[0].severity).toBe('high');
  });

  test('serialises reproductionSteps and consoleErrors', () => {
    const run = makeRun();
    createRun(run);

    const bug = makeBug(run.id, {
      reproductionSteps: ['Step A', 'Step B', 'Step C'],
      consoleErrors: ['Uncaught TypeError: Cannot read property'],
    });
    createBugReport(bug);

    const found = getBugsByRun(run.id).find((b) => b.id === bug.id);
    expect(found?.reproductionSteps).toEqual(['Step A', 'Step B', 'Step C']);
    expect(found?.consoleErrors).toEqual(['Uncaught TypeError: Cannot read property']);
  });
});
