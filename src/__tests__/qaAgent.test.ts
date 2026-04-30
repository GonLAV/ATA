import { QAAgent } from '../agents/QAAgent';
import { resetConfigForTests } from '../config/Config';
import type { Dashboard } from '../types';

jest.mock('../database/Repository', () => ({
  createRun: jest.fn(),
  updateRun: jest.fn(),
  getRun: jest.fn(),
  createScenario: jest.fn(),
  updateScenario: jest.fn(),
  createBugReport: jest.fn(),
  createProductRiskSignal: jest.fn(),
  getBugsByRun: jest.fn(() => []),
  getScenariosByRun: jest.fn(() => []),
  getRiskSignalsByRun: jest.fn(() => []),
}));

const originalEnv = { ...process.env };

afterEach(() => {
  process.env = { ...originalEnv };
  resetConfigForTests();
  jest.restoreAllMocks();
});

describe('QAAgent run queue', () => {
  test('keeps additional runs pending until active run capacity is available', async () => {
    process.env.MAX_CONCURRENT_RUNS = '1';
    resetConfigForTests();

    const startedUrls: string[] = [];
    const resolvers: Array<(dashboard: Dashboard) => void> = [];
    const executeSpy = jest.spyOn(
      QAAgent.prototype as unknown as { execute: (runId: string, url: string) => Promise<Dashboard> },
      'execute',
    );

    executeSpy.mockImplementation((_runId, url) => new Promise<Dashboard>((resolve) => {
      startedUrls.push(url);
      resolvers.push(resolve);
    }));

    const agent = new QAAgent();
    const first = agent.runSync('https://first.example');
    const second = agent.runSync('https://second.example');

    await flushPromises();
    expect(startedUrls).toEqual(['https://first.example']);

    resolvers[0](dashboardFor('https://first.example'));
    await first;
    await flushPromises();

    expect(startedUrls).toEqual(['https://first.example', 'https://second.example']);

    resolvers[1](dashboardFor('https://second.example'));
    await expect(second).resolves.toMatchObject({ url: 'https://second.example' });
  });

  test('starts runs up to the configured concurrency limit', async () => {
    process.env.MAX_CONCURRENT_RUNS = '2';
    resetConfigForTests();

    const startedUrls: string[] = [];
    const executeSpy = jest.spyOn(
      QAAgent.prototype as unknown as { execute: (runId: string, url: string) => Promise<Dashboard> },
      'execute',
    );

    executeSpy.mockImplementation((_runId, url) => new Promise<Dashboard>(() => {
      startedUrls.push(url);
    }));

    const agent = new QAAgent();
    void agent.runSync('https://first.example');
    void agent.runSync('https://second.example');
    void agent.runSync('https://third.example');

    await flushPromises();

    expect(startedUrls).toEqual(['https://first.example', 'https://second.example']);
  });
});

function dashboardFor(url: string): Dashboard {
  return {
    runId: url,
    url,
    status: 'completed',
    startedAt: new Date().toISOString(),
    completedAt: new Date().toISOString(),
    totalScenarios: 0,
    passedScenarios: 0,
    failedScenarios: 0,
    skippedScenarios: 0,
    bugsFound: 0,
    severityCounts: { critical: 0, high: 0, medium: 0, low: 0 },
    riskScore: 100,
    bugs: [],
    scenarios: [],
    productRiskSignals: [],
    releaseGate: {
      runId: url,
      url,
      evaluatedAt: new Date().toISOString(),
      decision: 'ship',
      ciExitCode: 0,
      confidence: 1,
      summary: 'Test dashboard',
      thresholds: {
        minRiskScore: 75,
        maxHighSeverityBugs: 0,
        maxHighSeverityRisks: 2,
        maxFailedScenarioRate: 0.25,
      },
      checks: [],
      requiredActions: [],
    },
    coverageAreas: [],
  };
}

function flushPromises(): Promise<void> {
  return new Promise((resolve) => setImmediate(resolve));
}