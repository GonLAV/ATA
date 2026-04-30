/**
 * Tests for the REST API routes.
 */
import request from 'supertest';
import { createApp } from '../api/server';

// Use in-memory database for tests
process.env.DATABASE_PATH = ':memory:';
process.env.OPENAI_API_KEY = 'test-key';

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
});

describe('POST /api/runs', () => {
  test('accepts a valid URL and returns 202 with runId', async () => {
    const res = await request(app)
      .post('/api/runs')
      .send({ url: 'https://example.com' });

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
