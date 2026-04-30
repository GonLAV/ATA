import { Router, Request, Response } from 'express';
import { z } from 'zod';
import { QAAgent } from '../agents/QAAgent';
import {
  getRun,
  getAllRuns,
  getScenariosByRun,
  getBugsByRun,
} from '../database/Repository';
import { buildDashboard, BugReporter } from '../reporters/BugReporter';
import type { CreateRunResponse } from '../types';

const router = Router();

// Schema validation
const CreateRunSchema = z.object({
  url: z.string().url('Must be a valid URL'),
});

// Shared agent instance (singleton per process)
const agent = new QAAgent();

// ─── POST /api/runs ──────────────────────────────────────────────────────────
// Trigger a new QA test run for a given URL.
router.post('/runs', async (req: Request, res: Response): Promise<void> => {
  const parsed = CreateRunSchema.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: parsed.error.errors[0].message });
    return;
  }

  const { url } = parsed.data;

  try {
    const runId = await agent.startRun(url);
    const body: CreateRunResponse = {
      runId,
      message: `QA run started. Poll GET /api/runs/${runId} for status.`,
    };
    res.status(202).json(body);
  } catch (err) {
    console.error('[API] Failed to start run:', err);
    res.status(500).json({ error: 'Failed to start QA run.' });
  }
});

// ─── GET /api/runs ────────────────────────────────────────────────────────────
// List all test runs.
router.get('/runs', (_req: Request, res: Response): void => {
  try {
    const runs = getAllRuns();
    res.json(runs);
  } catch (err) {
    console.error('[API] Failed to list runs:', err);
    res.status(500).json({ error: 'Failed to retrieve runs.' });
  }
});

// ─── GET /api/runs/:id ────────────────────────────────────────────────────────
// Get the status and summary of a specific test run.
router.get('/runs/:id', (req: Request, res: Response): void => {
  const run = getRun(req.params.id);
  if (!run) {
    res.status(404).json({ error: 'Run not found.' });
    return;
  }
  res.json(run);
});

// ─── GET /api/runs/:id/dashboard ─────────────────────────────────────────────
// Get the full structured dashboard (JSON) for a completed run.
router.get('/runs/:id/dashboard', (req: Request, res: Response): void => {
  const run = getRun(req.params.id);
  if (!run) {
    res.status(404).json({ error: 'Run not found.' });
    return;
  }

  const scenarios = getScenariosByRun(run.id);
  const bugs = getBugsByRun(run.id);
  const dashboard = buildDashboard(run, scenarios, bugs);
  res.json(dashboard);
});

// ─── GET /api/runs/:id/report ─────────────────────────────────────────────────
// Get the full Markdown report for a completed run.
router.get('/runs/:id/report', (req: Request, res: Response): void => {
  const run = getRun(req.params.id);
  if (!run) {
    res.status(404).json({ error: 'Run not found.' });
    return;
  }

  const scenarios = getScenariosByRun(run.id);
  const bugs = getBugsByRun(run.id);
  const dashboard = buildDashboard(run, scenarios, bugs);
  const markdown = BugReporter.renderDashboard(dashboard);

  res.setHeader('Content-Type', 'text/markdown; charset=utf-8');
  res.send(markdown);
});

// ─── GET /api/runs/:id/bugs ───────────────────────────────────────────────────
// Get all bugs found in a specific run.
router.get('/runs/:id/bugs', (req: Request, res: Response): void => {
  const run = getRun(req.params.id);
  if (!run) {
    res.status(404).json({ error: 'Run not found.' });
    return;
  }

  const bugs = getBugsByRun(run.id);
  res.json(bugs);
});

// ─── GET /api/runs/:id/scenarios ─────────────────────────────────────────────
// Get all test scenarios for a specific run.
router.get('/runs/:id/scenarios', (req: Request, res: Response): void => {
  const run = getRun(req.params.id);
  if (!run) {
    res.status(404).json({ error: 'Run not found.' });
    return;
  }

  const scenarios = getScenariosByRun(run.id);
  res.json(scenarios);
});

export default router;
