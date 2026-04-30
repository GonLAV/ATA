import { Router, Request, Response } from 'express';
import { z } from 'zod';
import { QAAgent } from '../agents/QAAgent';
import {
  getRun,
  getAllRuns,
  getScenariosByRun,
  getBugsByRun,
  getRiskSignalsByRun,
} from '../database/Repository';
import { buildDashboard, BugReporter } from '../reporters/BugReporter';
import { RegressionContractBuilder } from '../contracts/RegressionContractBuilder';
import { ReleaseGateEvaluator } from '../gates/ReleaseGateEvaluator';
import { observability } from '../observability/Observability';
import { getConfig } from '../config/Config';
import type { BugReport, CreateRunResponse, ProductRiskSignal, TestRun, TestScenario } from '../types';

const router = Router();

const CreateRunSchema = z.object({
  url: z.string().url('Must be a valid URL').transform((value) => new URL(value).toString()),
});

interface RunArtifacts {
  run: TestRun;
  scenarios: TestScenario[];
  bugs: BugReport[];
  risks: ProductRiskSignal[];
}

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
  if (!isAllowedTargetUrl(url)) {
    res.status(400).json({ error: 'URL targets private or local network resources. Set ALLOW_PRIVATE_TARGETS=true only in trusted environments.' });
    return;
  }

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
  const artifacts = loadRunArtifacts(req.params.id, res);
  if (!artifacts) {
    return;
  }

  const dashboard = buildDashboard(artifacts.run, artifacts.scenarios, artifacts.bugs, artifacts.risks);
  res.json(dashboard);
});

// ─── GET /api/runs/:id/report ─────────────────────────────────────────────────
// Get the full Markdown report for a completed run.
router.get('/runs/:id/report', (req: Request, res: Response): void => {
  const artifacts = loadRunArtifacts(req.params.id, res);
  if (!artifacts) {
    return;
  }

  const dashboard = buildDashboard(artifacts.run, artifacts.scenarios, artifacts.bugs, artifacts.risks);
  const markdown = BugReporter.renderDashboard(dashboard);

  res.setHeader('Content-Type', 'text/markdown; charset=utf-8');
  res.send(markdown);
});

// ─── GET /api/runs/:id/bugs ───────────────────────────────────────────────────
// Get all bugs found in a specific run.
router.get('/runs/:id/bugs', (req: Request, res: Response): void => {
  const artifacts = loadRunArtifacts(req.params.id, res);
  if (!artifacts) {
    return;
  }

  res.json(artifacts.bugs);
});

// ─── GET /api/runs/:id/scenarios ─────────────────────────────────────────────
// Get all test scenarios for a specific run.
router.get('/runs/:id/scenarios', (req: Request, res: Response): void => {
  const artifacts = loadRunArtifacts(req.params.id, res);
  if (!artifacts) {
    return;
  }

  res.json(artifacts.scenarios);
});

// ─── GET /api/runs/:id/risks ────────────────────────────────────────────────
// Get all Product Risk Radar signals for a specific run.
router.get('/runs/:id/risks', (req: Request, res: Response): void => {
  const artifacts = loadRunArtifacts(req.params.id, res);
  if (!artifacts) {
    return;
  }

  res.json(artifacts.risks);
});

// ─── GET /api/runs/:id/regression-contract ─────────────────────────────────
// Generate a Playwright regression contract from bugs and risk signals.
router.get('/runs/:id/regression-contract', (req: Request, res: Response): void => {
  const artifacts = loadRunArtifacts(req.params.id, res);
  if (!artifacts) {
    return;
  }

  const contract = new RegressionContractBuilder().build(artifacts.run, artifacts.scenarios, artifacts.bugs, artifacts.risks);
  res.json(contract);
});

// ─── GET /api/runs/:id/regression-spec ─────────────────────────────────────
// Download the generated Playwright spec directly for CI adoption.
router.get('/runs/:id/regression-spec', (req: Request, res: Response): void => {
  const artifacts = loadRunArtifacts(req.params.id, res);
  if (!artifacts) {
    return;
  }

  const contract = new RegressionContractBuilder().build(artifacts.run, artifacts.scenarios, artifacts.bugs, artifacts.risks);

  res.setHeader('Content-Type', 'text/typescript; charset=utf-8');
  res.setHeader('Content-Disposition', `attachment; filename="${contract.specFilename}"`);
  res.send(contract.spec);
});

// ─── GET /api/runs/:id/release-gate ────────────────────────────────────────
// Evaluate whether the run is safe to ship, warn, or block in CI/CD.
router.get('/runs/:id/release-gate', (req: Request, res: Response): void => {
  const artifacts = loadRunArtifacts(req.params.id, res);
  if (!artifacts) {
    return;
  }

  const gate = new ReleaseGateEvaluator().evaluate(artifacts.run, artifacts.scenarios, artifacts.bugs, artifacts.risks);
  res.json(gate);
});

// ─── GET /api/runs/:id/release-gate/ci ─────────────────────────────────────
// CI-friendly endpoint with explicit release decision and desired exit code.
router.get('/runs/:id/release-gate/ci', (req: Request, res: Response): void => {
  const artifacts = loadRunArtifacts(req.params.id, res);
  if (!artifacts) {
    return;
  }

  const gate = new ReleaseGateEvaluator().evaluate(artifacts.run, artifacts.scenarios, artifacts.bugs, artifacts.risks);
  res.status(gate.decision === 'block' ? 409 : 200).json({
    decision: gate.decision,
    ciExitCode: gate.ciExitCode,
    confidence: gate.confidence,
    summary: gate.summary,
    requiredActions: gate.requiredActions,
  });
});

// ─── GET /api/metrics ───────────────────────────────────────────────────────
// Lightweight observability endpoint for local runs and CI smoke checks.
router.get('/metrics', (_req: Request, res: Response): void => {
  res.json(observability.metrics());
});

function loadRunArtifacts(runId: string, res: Response): RunArtifacts | undefined {
  const run = getRun(runId);
  if (!run) {
    res.status(404).json({ error: 'Run not found.' });
    return undefined;
  }

  return {
    run,
    scenarios: getScenariosByRun(run.id),
    bugs: getBugsByRun(run.id),
    risks: getRiskSignalsByRun(run.id),
  };
}

function isAllowedTargetUrl(url: string): boolean {
  const parsed = new URL(url);
  if (!['http:', 'https:'].includes(parsed.protocol)) return false;
  if (getConfig().allowPrivateTargets) return true;
  return !isPrivateHostname(parsed.hostname);
}

function isPrivateHostname(hostname: string): boolean {
  const normalized = hostname.toLowerCase();
  if (normalized === 'localhost' || normalized.endsWith('.localhost')) return true;
  if (normalized === '::1' || normalized.startsWith('fe80:') || normalized.startsWith('fc') || normalized.startsWith('fd')) return true;

  const parts = normalized.split('.').map((part) => Number.parseInt(part, 10));
  if (parts.length !== 4 || parts.some((part) => Number.isNaN(part))) return false;

  const [first, second] = parts;
  return first === 10
    || first === 127
    || (first === 172 && second >= 16 && second <= 31)
    || (first === 192 && second === 168)
    || (first === 169 && second === 254)
    || first === 0;
}

export default router;
