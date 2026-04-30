/**
 * Tests for the REST API routes.
 */
import request from 'supertest';
import { createApp } from '../api/server';
import { resetConfigForTests } from '../config/Config';

// Use in-memory database for tests
process.env.DATABASE_PATH = ':memory:';
process.env.OPENAI_API_KEY = 'test-key';
delete process.env.QA_COPILOT_API_KEY;
delete process.env.ALLOW_PRIVATE_TARGETS;

// We mock the QAAgent so we don't spin up a real browser
jest.mock('../agents/QAAgent', () => {
  return {
    QAAgent: jest.fn().mockImplementation(() => ({
      startRun: jest.fn().mockResolvedValue('mock-run-id'),
    })),
  };
});

// We also need to mock the Repository so getRun returns a consistent result
jest.mock('../database/Repository', () => {
  const original = jest.requireActual('../database/Repository') as Record<string, unknown>;
  return {
    ...original,
    getRun: jest.fn().mockImplementation((id: string) => {
      if (id === 'mock-run-id') {
        return {
          id: 'mock-run-id',
          url: 'https://example.com',
          status: 'running',
          startedAt: new Date().toISOString(),
          totalScenarios: 0,
          passedScenarios: 0,
          failedScenarios: 0,
          bugsFound: 0,
        };
      }
      return undefined;
    }),
    getAllRuns: jest.fn().mockReturnValue([]),
    getScenariosByRun: jest.fn().mockReturnValue([]),
    getBugsByRun: jest.fn().mockReturnValue([]),
    getRiskSignalsByRun: jest.fn().mockReturnValue([]),
  };
});

const app = createApp();

describe('GET /health', () => {
  test('returns 200 with status ok', async () => {
    const res = await request(app).get('/health');
    expect(res.status).toBe(200);
    expect(res.body.status).toBe('ok');
  });

  test('sets baseline security headers and request id', async () => {
    const res = await request(app).get('/health');
    expect(res.headers['x-request-id']).toBeDefined();
    expect(res.headers['x-content-type-options']).toBe('nosniff');
    expect(res.headers['x-frame-options']).toBe('DENY');
    expect(res.headers['content-security-policy']).toContain("default-src 'none'");
  });
});

describe('POST /api/runs', () => {
  test('accepts a valid URL and returns 202 with runId', async () => {
    process.env.ALLOW_PRIVATE_TARGETS = 'true';
    resetConfigForTests();

    const res = await request(app)
      .post('/api/runs')
      .send({ url: 'https://example.com' });

    delete process.env.ALLOW_PRIVATE_TARGETS;
    resetConfigForTests();

    expect(res.status).toBe(202);
    expect(res.body.runId).toBe('mock-run-id');
    expect(res.body.message).toBeDefined();
  });

  test('returns 400 for a missing URL', async () => {
    const res = await request(app).post('/api/runs').send({});
    expect(res.status).toBe(400);
    expect(res.body.error).toBeDefined();
  });

  test('returns 400 for an invalid URL', async () => {
    const res = await request(app).post('/api/runs').send({ url: 'not-a-url' });
    expect(res.status).toBe(400);
  });

  test('returns 400 for malformed JSON', async () => {
    const res = await request(app)
      .post('/api/runs')
      .set('Content-Type', 'application/json')
      .send('{"url":');
    expect(res.status).toBe(400);
    expect(res.body.error).toContain('Malformed JSON');
  });

  test('blocks private targets unless explicitly allowed', async () => {
    resetConfigForTests();
    const res = await request(app).post('/api/runs').send({ url: 'http://127.0.0.1:3000' });
    expect(res.status).toBe(400);
    expect(res.body.error).toContain('private or local');
  });
});

describe('API authentication', () => {
  const previousApiKey = process.env.QA_COPILOT_API_KEY;

  beforeAll(() => {
    process.env.QA_COPILOT_API_KEY = 'test-api-key-12345';
    resetConfigForTests();
  });

  afterAll(() => {
    if (previousApiKey === undefined) {
      delete process.env.QA_COPILOT_API_KEY;
    } else {
      process.env.QA_COPILOT_API_KEY = previousApiKey;
    }
    resetConfigForTests();
  });

  test('rejects API requests without a configured key', async () => {
    const securedApp = createApp();
    const res = await request(securedApp).get('/api/runs');
    expect(res.status).toBe(401);
  });

  test('accepts API requests with x-qa-copilot-api-key', async () => {
    const securedApp = createApp();
    const res = await request(securedApp)
      .get('/api/runs')
      .set('x-qa-copilot-api-key', 'test-api-key-12345');
    expect(res.status).toBe(200);
  });
});

describe('API rate limiting', () => {
  const previousMax = process.env.API_RATE_LIMIT_MAX;
  const previousWindow = process.env.API_RATE_LIMIT_WINDOW_MS;

  beforeAll(() => {
    process.env.API_RATE_LIMIT_MAX = '1';
    process.env.API_RATE_LIMIT_WINDOW_MS = '60000';
    resetConfigForTests();
  });

  afterAll(() => {
    if (previousMax === undefined) {
      delete process.env.API_RATE_LIMIT_MAX;
    } else {
      process.env.API_RATE_LIMIT_MAX = previousMax;
    }

    if (previousWindow === undefined) {
      delete process.env.API_RATE_LIMIT_WINDOW_MS;
    } else {
      process.env.API_RATE_LIMIT_WINDOW_MS = previousWindow;
    }
    resetConfigForTests();
  });

  test('returns 429 after the configured request budget is exhausted', async () => {
    const limitedApp = createApp();

    const first = await request(limitedApp).get('/api/runs');
    const second = await request(limitedApp).get('/api/runs');

    expect(first.status).toBe(200);
    expect(second.status).toBe(429);
    expect(second.headers['retry-after']).toBeDefined();
  });
});

describe('GET /api/runs', () => {
  test('returns an array', async () => {
    const res = await request(app).get('/api/runs');
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });
});

describe('GET /api/runs/:id', () => {
  test('returns run data for a known run', async () => {
    const res = await request(app).get('/api/runs/mock-run-id');
    expect(res.status).toBe(200);
    expect(res.body.id).toBe('mock-run-id');
    expect(res.body.url).toBe('https://example.com');
  });

  test('returns 404 for an unknown run', async () => {
    const res = await request(app).get('/api/runs/does-not-exist');
    expect(res.status).toBe(404);
  });
});

describe('GET /api/runs/:id/dashboard', () => {
  test('returns a dashboard object for a known run', async () => {
    const res = await request(app).get('/api/runs/mock-run-id/dashboard');
    expect(res.status).toBe(200);
    expect(res.body.runId).toBe('mock-run-id');
    expect(res.body).toHaveProperty('severityCounts');
    expect(res.body).toHaveProperty('bugs');
    expect(res.body).toHaveProperty('scenarios');
  });
});

describe('GET /api/runs/:id/report', () => {
  test('returns a Markdown report for a known run', async () => {
    const res = await request(app).get('/api/runs/mock-run-id/report');
    expect(res.status).toBe(200);
    expect(res.type).toMatch(/markdown/);
    expect(res.text).toContain('QA Copilot');
  });
});

describe('GET /api/runs/:id/bugs', () => {
  test('returns an array for a known run', async () => {
    const res = await request(app).get('/api/runs/mock-run-id/bugs');
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });
});

describe('GET /api/runs/:id/scenarios', () => {
  test('returns an array for a known run', async () => {
    const res = await request(app).get('/api/runs/mock-run-id/scenarios');
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });
});

describe('GET /api/runs/:id/risks', () => {
  test('returns an array for a known run', async () => {
    const res = await request(app).get('/api/runs/mock-run-id/risks');
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });
});

describe('GET /api/runs/:id/regression-contract', () => {
  test('returns a generated Playwright regression contract', async () => {
    const res = await request(app).get('/api/runs/mock-run-id/regression-contract');
    expect(res.status).toBe(200);
    expect(res.body.framework).toBe('playwright');
    expect(res.body.specFilename).toContain('qa-copilot-mock-run-id');
    expect(res.body.spec).toContain('baseline page remains reachable');
  });
});

describe('GET /api/runs/:id/regression-spec', () => {
  test('returns the raw Playwright spec', async () => {
    const res = await request(app).get('/api/runs/mock-run-id/regression-spec');
    expect(res.status).toBe(200);
    expect(res.text).toContain("import { test, expect } from '@playwright/test'");
    expect(res.headers['content-disposition']).toContain('qa-copilot-mock-run-id');
  });
});

describe('GET /api/runs/:id/release-gate', () => {
  test('returns the autonomous release gate decision', async () => {
    const res = await request(app).get('/api/runs/mock-run-id/release-gate');
    expect(res.status).toBe(200);
    expect(res.body.decision).toBe('block');
    expect(res.body).toHaveProperty('checks');
    expect(res.body).toHaveProperty('requiredActions');
  });
});

describe('GET /api/runs/:id/release-gate/ci', () => {
  test('returns CI-friendly gate status and exit code', async () => {
    const res = await request(app).get('/api/runs/mock-run-id/release-gate/ci');
    expect(res.status).toBe(409);
    expect(res.body.decision).toBe('block');
    expect(res.body.ciExitCode).toBe(1);
  });
});

describe('GET /api/metrics', () => {
  test('returns counters and recent events', async () => {
    const res = await request(app).get('/api/metrics');
    expect(res.status).toBe(200);
    expect(res.body).toHaveProperty('counters');
    expect(res.body).toHaveProperty('recentEvents');
  });
});

describe('GET /undefined-route', () => {
  test('returns 404', async () => {
    const res = await request(app).get('/unknown/path');
    expect(res.status).toBe(404);
  });
});
