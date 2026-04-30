import { getDatabase } from './Database';
import type {
  TestRun,
  TestScenario,
  BugReport,
  ProductRiskSignal,
  RunStatus,
} from '../types';

// ─── Test Runs ────────────────────────────────────────────────────────────────

export function createRun(run: TestRun): TestRun {
  const db = getDatabase();
  db.prepare(`
    INSERT INTO test_runs (id, url, status, started_at, completed_at,
      total_scenarios, passed_scenarios, failed_scenarios, bugs_found)
    VALUES (@id, @url, @status, @startedAt, @completedAt,
      @totalScenarios, @passedScenarios, @failedScenarios, @bugsFound)
  `).run({
    id: run.id,
    url: run.url,
    status: run.status,
    startedAt: run.startedAt,
    completedAt: run.completedAt ?? null,
    totalScenarios: run.totalScenarios,
    passedScenarios: run.passedScenarios,
    failedScenarios: run.failedScenarios,
    bugsFound: run.bugsFound,
  });
  return run;
}

export function updateRun(id: string, updates: Partial<TestRun>): void {
  const db = getDatabase();
  const fields: string[] = [];
  const params: Record<string, unknown> = { id };

  if (updates.status !== undefined) {
    fields.push('status = @status');
    params.status = updates.status;
  }
  if (updates.completedAt !== undefined) {
    fields.push('completed_at = @completedAt');
    params.completedAt = updates.completedAt;
  }
  if (updates.totalScenarios !== undefined) {
    fields.push('total_scenarios = @totalScenarios');
    params.totalScenarios = updates.totalScenarios;
  }
  if (updates.passedScenarios !== undefined) {
    fields.push('passed_scenarios = @passedScenarios');
    params.passedScenarios = updates.passedScenarios;
  }
  if (updates.failedScenarios !== undefined) {
    fields.push('failed_scenarios = @failedScenarios');
    params.failedScenarios = updates.failedScenarios;
  }
  if (updates.bugsFound !== undefined) {
    fields.push('bugs_found = @bugsFound');
    params.bugsFound = updates.bugsFound;
  }

  if (fields.length === 0) return;
  db.prepare(`UPDATE test_runs SET ${fields.join(', ')} WHERE id = @id`).run(params);
}

export function getRun(id: string): TestRun | undefined {
  const db = getDatabase();
  const row = db.prepare('SELECT * FROM test_runs WHERE id = ?').get(id) as
    | Record<string, unknown>
    | undefined;
  return row ? rowToRun(row) : undefined;
}

export function getAllRuns(): TestRun[] {
  const db = getDatabase();
  const rows = db.prepare('SELECT * FROM test_runs ORDER BY started_at DESC').all() as
    Record<string, unknown>[];
  return rows.map(rowToRun);
}

function rowToRun(row: Record<string, unknown>): TestRun {
  return {
    id: row.id as string,
    url: row.url as string,
    status: row.status as RunStatus,
    startedAt: row.started_at as string,
    completedAt: row.completed_at as string | undefined,
    totalScenarios: row.total_scenarios as number,
    passedScenarios: row.passed_scenarios as number,
    failedScenarios: row.failed_scenarios as number,
    bugsFound: row.bugs_found as number,
  };
}

// ─── Test Scenarios ───────────────────────────────────────────────────────────

export function createScenario(scenario: TestScenario): TestScenario {
  const db = getDatabase();
  db.prepare(`
    INSERT INTO test_scenarios (id, run_id, title, description, priority, steps_json, status, duration_ms)
    VALUES (@id, @runId, @title, @description, @priority, @stepsJson, @status, @durationMs)
  `).run({
    id: scenario.id,
    runId: scenario.runId,
    title: scenario.title,
    description: scenario.description,
    priority: scenario.priority,
    stepsJson: JSON.stringify(scenario.steps),
    status: scenario.status,
    durationMs: scenario.durationMs ?? null,
  });
  return scenario;
}

export function updateScenario(id: string, updates: Partial<TestScenario>): void {
  const db = getDatabase();
  const fields: string[] = [];
  const params: Record<string, unknown> = { id };

  if (updates.status !== undefined) {
    fields.push('status = @status');
    params.status = updates.status;
  }
  if (updates.durationMs !== undefined) {
    fields.push('duration_ms = @durationMs');
    params.durationMs = updates.durationMs;
  }

  if (fields.length === 0) return;
  db.prepare(`UPDATE test_scenarios SET ${fields.join(', ')} WHERE id = @id`).run(params);
}

export function getScenariosByRun(runId: string): TestScenario[] {
  const db = getDatabase();
  const rows = db.prepare('SELECT * FROM test_scenarios WHERE run_id = ? ORDER BY created_at').all(runId) as
    Record<string, unknown>[];
  return rows.map(rowToScenario);
}

function rowToScenario(row: Record<string, unknown>): TestScenario {
  return {
    id: row.id as string,
    runId: row.run_id as string,
    title: row.title as string,
    description: row.description as string,
    priority: row.priority as 'high' | 'medium' | 'low',
    steps: JSON.parse(row.steps_json as string),
    status: row.status as TestScenario['status'],
    durationMs: row.duration_ms as number | undefined,
  };
}

// ─── Bug Reports ──────────────────────────────────────────────────────────────

export function createBugReport(bug: BugReport): BugReport {
  const db = getDatabase();
  db.prepare(`
    INSERT INTO bug_reports
      (id, run_id, scenario_id, title, severity, description,
       reproduction_steps, expected_behavior, actual_behavior,
       screenshot_path, url, detected_at, console_errors, network_errors, error_stack)
    VALUES
      (@id, @runId, @scenarioId, @title, @severity, @description,
       @reproductionSteps, @expectedBehavior, @actualBehavior,
       @screenshotPath, @url, @detectedAt, @consoleErrors, @networkErrors, @errorStack)
  `).run({
    id: bug.id,
    runId: bug.runId,
    scenarioId: bug.scenarioId ?? null,
    title: bug.title,
    severity: bug.severity,
    description: bug.description,
    reproductionSteps: JSON.stringify(bug.reproductionSteps),
    expectedBehavior: bug.expectedBehavior,
    actualBehavior: bug.actualBehavior,
    screenshotPath: bug.screenshotPath ?? null,
    url: bug.url,
    detectedAt: bug.detectedAt,
    consoleErrors: bug.consoleErrors ? JSON.stringify(bug.consoleErrors) : null,
    networkErrors: bug.networkErrors ? JSON.stringify(bug.networkErrors) : null,
    errorStack: bug.errorStack ?? null,
  });
  return bug;
}

export function getBugsByRun(runId: string): BugReport[] {
  const db = getDatabase();
  const rows = db.prepare('SELECT * FROM bug_reports WHERE run_id = ? ORDER BY detected_at').all(runId) as
    Record<string, unknown>[];
  return rows.map(rowToBug);
}

function rowToBug(row: Record<string, unknown>): BugReport {
  return {
    id: row.id as string,
    runId: row.run_id as string,
    scenarioId: row.scenario_id as string | undefined,
    title: row.title as string,
    severity: row.severity as BugReport['severity'],
    description: row.description as string,
    reproductionSteps: JSON.parse(row.reproduction_steps as string),
    expectedBehavior: row.expected_behavior as string,
    actualBehavior: row.actual_behavior as string,
    screenshotPath: row.screenshot_path as string | undefined,
    url: row.url as string,
    detectedAt: row.detected_at as string,
    consoleErrors: row.console_errors
      ? JSON.parse(row.console_errors as string)
      : undefined,
    networkErrors: row.network_errors
      ? JSON.parse(row.network_errors as string)
      : undefined,
    errorStack: row.error_stack as string | undefined,
  };
}

// ─── Product Risk Signals ───────────────────────────────────────────────────

export function createProductRiskSignal(signal: ProductRiskSignal): ProductRiskSignal {
  const db = getDatabase();
  db.prepare(`
    INSERT INTO product_risk_signals
      (id, run_id, scenario_id, type, title, severity, evidence, recommendation, url, detected_at)
    VALUES
      (@id, @runId, @scenarioId, @type, @title, @severity, @evidence, @recommendation, @url, @detectedAt)
  `).run({
    id: signal.id,
    runId: signal.runId,
    scenarioId: signal.scenarioId ?? null,
    type: signal.type,
    title: signal.title,
    severity: signal.severity,
    evidence: JSON.stringify(signal.evidence),
    recommendation: signal.recommendation,
    url: signal.url,
    detectedAt: signal.detectedAt,
  });
  return signal;
}

export function getRiskSignalsByRun(runId: string): ProductRiskSignal[] {
  const db = getDatabase();
  const rows = db.prepare('SELECT * FROM product_risk_signals WHERE run_id = ? ORDER BY detected_at').all(runId) as
    Record<string, unknown>[];
  return rows.map(rowToProductRiskSignal);
}

function rowToProductRiskSignal(row: Record<string, unknown>): ProductRiskSignal {
  return {
    id: row.id as string,
    runId: row.run_id as string,
    scenarioId: row.scenario_id as string | undefined,
    type: row.type as ProductRiskSignal['type'],
    title: row.title as string,
    severity: row.severity as ProductRiskSignal['severity'],
    evidence: JSON.parse(row.evidence as string),
    recommendation: row.recommendation as string,
    url: row.url as string,
    detectedAt: row.detected_at as string,
  };
}
