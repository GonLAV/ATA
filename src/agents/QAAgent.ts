import { randomUUID } from 'crypto';
import type { Page, Request, Response } from 'playwright';

import { BrowserManager } from '../browser/BrowserManager';
import { PageExplorer } from '../browser/PageExplorer';
import { ScreenshotManager } from '../browser/ScreenshotManager';
import { LLMClient } from './LLMClient';
import { ProductRiskRadar } from './ProductRiskRadar';
import { TestMutator } from '../mutation/TestMutator';
import { RedTeamEngine } from '../redteam/RedTeamEngine';
import {
  createRun,
  updateRun,
  getRun,
  createScenario,
  updateScenario,
  createBugReport,
  createProductRiskSignal,
  getBugsByRun,
  getScenariosByRun,
  getRiskSignalsByRun,
} from '../database/Repository';
import { buildDashboard } from '../reporters/BugReporter';
import { getConfig } from '../config/Config';
import { observability } from '../observability/Observability';
import type {
  ActionObservation,
  ExplorationMap,
  TestRun,
  TestScenario,
  TestStep,
  BugReport,
  Dashboard,
  PageSnapshot,
} from '../types';

/**
 * The main QA Agent orchestrator.
 *
 * Flow:
 *  1. Explore the target URL → PageSnapshot
 *  2. Ask the LLM to generate TestScenarios from the snapshot
 *  3. Execute each scenario in an isolated browser context
 *  4. Detect issues (console errors, network failures, thrown exceptions)
 *  5. Ask the LLM to classify each failure as a BugReport
 *  6. Persist everything and return a Dashboard
 */
export class QAAgent {
  private browserManager: BrowserManager;
  private screenshotManager: ScreenshotManager;
  private explorer: PageExplorer;
  private llm: LLMClient;
  private riskRadar: ProductRiskRadar;
  private mutator: TestMutator;
  private redTeam: RedTeamEngine;
  private stepTimeoutMs: number;
  private discoveryPageLimit: number;
  private maxScenarios: number;

  constructor() {
    this.browserManager = new BrowserManager();
    this.screenshotManager = new ScreenshotManager();
    this.explorer = new PageExplorer(this.screenshotManager);
    this.llm = new LLMClient();
    this.riskRadar = new ProductRiskRadar();
    this.mutator = new TestMutator();
    this.redTeam = new RedTeamEngine();
    const config = getConfig();
    this.stepTimeoutMs = config.stepTimeoutMs;
    this.discoveryPageLimit = config.discoveryPageLimit;
    this.maxScenarios = config.maxScenarios;
  }

  /**
   * Run a full QA session for the given URL.
   * Returns the run ID so callers can poll for results asynchronously.
   */
  async startRun(url: string): Promise<string> {
    const runId = this.createRunRecord(url);

    // Execute asynchronously so the HTTP response returns immediately
    this.execute(runId, url).catch((err) => {
      console.error(`[QAAgent] Run ${runId} crashed:`, err);
      updateRun(runId, { status: 'failed', completedAt: new Date().toISOString() });
      observability.emit('run.failed', { runId, payload: { error: (err as Error).message } });
    });

    return runId;
  }

  /**
   * Block until the run is complete and return the dashboard.
   * Useful for CLI / direct invocation.
   */
  async runSync(url: string): Promise<Dashboard> {
    const runId = this.createRunRecord(url);
    return this.execute(runId, url);
  }

  private createRunRecord(url: string): string {
    const runId = randomUUID();
    const run: TestRun = {
      id: runId,
      url,
      status: 'pending',
      startedAt: new Date().toISOString(),
      totalScenarios: 0,
      passedScenarios: 0,
      failedScenarios: 0,
      bugsFound: 0,
    };
    createRun(run);
    return runId;
  }

  // ─── Core execution pipeline ─────────────────────────────────────────────

  private async execute(runId: string, url: string): Promise<Dashboard> {
    updateRun(runId, { status: 'running' });
    observability.emit('run.started', { runId, payload: { url } });
    console.log(`[QAAgent] Starting run ${runId} for ${url}`);

    try {
      await this.browserManager.launch();

      // ── Step 1: Explore the page ──
      const explorationMap = await this.exploreApplication(url);
      const snapshot = explorationMap.snapshots[0];

      console.log(
        `[QAAgent] Snapshot: ${snapshot.buttons.length} buttons, ${snapshot.forms.length} forms, ${snapshot.links.length} links`,
      );

      for (const exploredSnapshot of explorationMap.snapshots) {
        const signals = this.riskRadar.analyzeSnapshot(runId, exploredSnapshot);
        for (const signal of signals) {
          createProductRiskSignal(signal);
          observability.emit('risk.detected', { runId, payload: { type: signal.type, severity: signal.severity } });
        }
      }

      // Log any immediately visible issues from the initial page load
      if (snapshot.consoleErrors.length > 0) {
        console.warn(`[QAAgent] Console errors on initial load:`, snapshot.consoleErrors);
      }

      // ── Step 2: Generate test scenarios ──
      const rawScenarios = (await this.llm.generateTestScenarios(explorationMap)).slice(0, this.maxScenarios);
      console.log(`[QAAgent] LLM generated ${rawScenarios.length} scenarios`);

      const standardScenarios: TestScenario[] = rawScenarios.map((s) => ({
        ...s,
        id: randomUUID(),
        runId,
        status: 'pending',
      }));

      // ── Step 2b: Append red team scenarios ──
      const redTeamScenarios = this.redTeam.generateScenarios(explorationMap, runId);
      console.log(`[QAAgent] Red team generated ${redTeamScenarios.length} adversarial scenarios`);

      const scenarios: TestScenario[] = [...standardScenarios, ...redTeamScenarios];

      for (const s of scenarios) {
        createScenario(s);
      }
      updateRun(runId, { totalScenarios: scenarios.length });

      // ── Step 3: Execute each scenario ──
      let passed = 0;
      let failed = 0;
      let bugsFound = 0;

      for (const scenario of scenarios) {
        console.log(`[QAAgent] Executing: "${scenario.title}"`);
        observability.emit('scenario.started', { runId, scenarioId: scenario.id, payload: { title: scenario.title } });
        const result = await this.executeScenario(runId, url, scenario);

        if (result.bug) {
          createBugReport(result.bug);
          bugsFound++;
          updateRun(runId, { bugsFound });
          observability.emit('bug.detected', { runId, scenarioId: scenario.id, payload: { severity: result.bug.severity } });
        }

        for (const signal of result.riskSignals) {
          createProductRiskSignal(signal);
          observability.emit('risk.detected', { runId, scenarioId: scenario.id, payload: { type: signal.type, severity: signal.severity } });
        }

        if (result.passed) {
          passed++;
        } else {
          failed++;
        }

        updateRun(runId, { passedScenarios: passed, failedScenarios: failed });
        observability.emit('scenario.completed', { runId, scenarioId: scenario.id, payload: { passed: result.passed } });
      }

      // ── Step 4: Finalise ──
      const completedAt = new Date().toISOString();
      updateRun(runId, { status: 'completed', completedAt });

      const dbRun = getRun(runId);
      const finalRun: TestRun = {
        id: runId,
        url,
        status: 'completed',
        startedAt: dbRun?.startedAt ?? new Date().toISOString(),
        completedAt,
        totalScenarios: scenarios.length,
        passedScenarios: passed,
        failedScenarios: failed,
        bugsFound,
      };

      const allScenarios = getScenariosByRun(runId);
      const allBugs = getBugsByRun(runId);
      const allRiskSignals = getRiskSignalsByRun(runId);
      observability.emit('run.completed', { runId, payload: { scenarios: scenarios.length, bugsFound, riskSignals: allRiskSignals.length } });
      return buildDashboard(finalRun, allScenarios, allBugs, allRiskSignals, explorationMap);
    } finally {
      await this.browserManager.close();
    }
  }

  private async exploreApplication(url: string): Promise<ExplorationMap> {
    const context = await this.browserManager.newContext();
    const snapshots: PageSnapshot[] = [];

    try {
      const firstSnapshot = await this.explorer.explore(context, url);
      snapshots.push(firstSnapshot);

      const sameOriginLinks = this.sameOriginLinks(firstSnapshot, url).slice(0, this.discoveryPageLimit - 1);
      for (const link of sameOriginLinks) {
        const snapshot = await this.explorer.explore(context, link);
        snapshots.push(snapshot);
      }
    } finally {
      await context.close();
    }

    return {
      entryUrl: url,
      snapshots,
      routes: Array.from(new Set(snapshots.map((snapshot) => snapshot.finalUrl ?? snapshot.url))),
      discoveredAt: new Date().toISOString(),
    };
  }

  private sameOriginLinks(snapshot: PageSnapshot, baseUrl: string): string[] {
    const base = new URL(baseUrl);
    const links = snapshot.links
      .map((link) => link.href)
      .filter((href): href is string => Boolean(href))
      .map((href) => {
        try {
          return new URL(href, base).toString();
        } catch {
          return undefined;
        }
      })
      .filter((href): href is string => Boolean(href))
      .filter((href) => {
        const parsed = new URL(href);
        return parsed.origin === base.origin && !parsed.hash;
      });

    return Array.from(new Set(links));
  }

  // ─── Scenario executor ────────────────────────────────────────────────────

  private async executeScenario(
    runId: string,
    baseUrl: string,
    scenario: TestScenario,
  ): Promise<{ passed: boolean; bug?: BugReport; riskSignals: ReturnType<ProductRiskRadar['analyzeScenario']> }> {
    const context = await this.browserManager.newContext();
    const page = await context.newPage();

    const consoleErrors: string[] = [];
    const networkErrors: string[] = [];
    const networkActivity: string[] = [];
    const executedSteps: string[] = [];
    const observations: ActionObservation[] = [];

    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });
    page.on('pageerror', (err) => {
      consoleErrors.push(err.message);
    });
    page.on('requestfailed', (req: Request) => {
      networkErrors.push(`${req.method()} ${req.url()} — ${req.failure()?.errorText ?? 'unknown'}`);
    });
    page.on('response', (res: Response) => {
      networkActivity.push(`${res.status()} ${res.url()}`);
      if (res.status() >= 400) {
        networkErrors.push(`HTTP ${res.status()} ${res.url()}`);
      }
    });

    const startTime = Date.now();
    let errorDescription: string | undefined;
    let errorStack: string | undefined;
    let screenshotPath: string | undefined;

    try {
      // Always start from base URL
      await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: this.stepTimeoutMs });
      executedSteps.push(`Navigate to ${baseUrl}`);

      for (const step of scenario.steps) {
        const observation = await this.executeStep(page, step, baseUrl, consoleErrors, networkErrors, networkActivity);
        observations.push(observation);
        executedSteps.push(step.description);
      }
    } catch (err) {
      const e = err as Error;
      errorDescription = e.message;
      errorStack = e.stack;
      console.warn(`[QAAgent] Scenario "${scenario.title}" failed:`, e.message);

      screenshotPath = await this.screenshotManager
        .capture(page, `bug_${scenario.id}`)
        .catch(() => undefined);
    } finally {
      await page.close().catch(() => undefined);
      await context.close().catch(() => undefined);
    }

    const durationMs = Date.now() - startTime;

    // Determine pass/fail
    const hasError =
      errorDescription !== undefined ||
      consoleErrors.length > 0 ||
      networkErrors.length > 0;

    const status: TestScenario['status'] = hasError ? 'failed' : 'passed';
    updateScenario(scenario.id, { status, durationMs });
    const riskSignals = this.riskRadar.analyzeScenario(runId, scenario, observations);

    if (!hasError) {
      return { passed: true, riskSignals };
    }

    // Classify bug via LLM
    const bugDetails = await this.llm.classifyBug(
      runId,
      scenario.id,
      page.url() || baseUrl,
      errorDescription ?? 'Console or network errors detected.',
      executedSteps,
      consoleErrors,
      networkErrors,
      errorStack,
    );

    const bug: BugReport = {
      id: randomUUID(),
      runId,
      scenarioId: scenario.id,
      detectedAt: new Date().toISOString(),
      screenshotPath,
      ...bugDetails,
    };

    return { passed: false, bug, riskSignals };
  }

  // ─── Step executor ────────────────────────────────────────────────────────

  private async executeStep(
    page: Page,
    step: TestStep,
    baseUrl: string,
    consoleErrors: string[],
    networkErrors: string[],
    networkActivity: string[],
  ): Promise<ActionObservation> {
    const timeout = this.stepTimeoutMs;
    const beforeUrl = page.url();
    const beforeText = await this.bodyText(page);
    const beforeConsoleCount = consoleErrors.length;
    const beforeNetworkErrorCount = networkErrors.length;
    const beforeNetworkActivityCount = networkActivity.length;
    const startedAt = Date.now();

    switch (step.action) {
      case 'navigate':
        await page.goto(step.value ?? baseUrl, { waitUntil: 'domcontentloaded', timeout });
        break;

      case 'click':
        if (step.selector) {
          await page.waitForSelector(step.selector, { timeout }).catch(() => undefined);
          await this.tryWithMutation(
            () => page.click(step.selector!, { timeout }),
            step,
            async (alt) => { await page.click(alt, { timeout }); },
          );
        }
        break;

      case 'fill':
        if (step.selector && step.value !== undefined) {
          await page.waitForSelector(step.selector, { timeout }).catch(() => undefined);
          await this.tryWithMutation(
            () => page.fill(step.selector!, step.value!, { timeout }),
            step,
            async (alt) => { await page.fill(alt, step.value!, { timeout }); },
          );
        }
        break;

      case 'submit':
        if (step.selector) {
          await page.waitForSelector(step.selector, { timeout }).catch(() => undefined);
          await page.click(step.selector, { timeout });
          await page.waitForLoadState('networkidle', { timeout }).catch(() => undefined);
        }
        break;

      case 'wait':
        await page.waitForTimeout(parseInt(step.value ?? '1000', 10));
        break;

      case 'screenshot':
        await this.screenshotManager
          .capture(page, step.value ?? `step_${Date.now()}`)
          .catch(() => undefined);
        break;

      case 'assert_visible':
        if (step.selector) {
          await page.waitForSelector(step.selector, { state: 'visible', timeout });
        }
        break;

      case 'assert_text':
        if (step.selector && step.value) {
          const text = await page.textContent(step.selector, { timeout }).catch(() => '');
          if (!text?.includes(step.value)) {
            throw new Error(
              `assert_text failed: "${text}" does not contain "${step.value}"`,
            );
          }
        }
        break;

      case 'assert_url':
        if (step.value) {
          const current = page.url();
          if (!current.includes(step.value)) {
            throw new Error(`assert_url failed: "${current}" does not include "${step.value}"`);
          }
        }
        break;

      default:
        console.warn(`[QAAgent] Unknown action type: ${(step as TestStep).action}`);
    }

    const afterText = await this.bodyText(page);
    const afterUrl = page.url();

    return {
      stepDescription: step.description,
      action: step.action,
      selector: step.selector,
      beforeUrl,
      afterUrl,
      urlChanged: beforeUrl !== afterUrl,
      domChanged: beforeText !== afterText,
      networkActivityDelta: networkActivity.length - beforeNetworkActivityCount,
      networkErrorsDelta: networkErrors.length - beforeNetworkErrorCount,
      consoleErrorsDelta: consoleErrors.length - beforeConsoleCount,
      durationMs: Date.now() - startedAt,
    };
  }

  /**
   * Attempts the primary action. On failure, asks TestMutator for alternative
   * selectors and retries each in order. Throws the original error only if
   * all candidates also fail.
   */
  private async tryWithMutation(
    primary: () => Promise<void>,
    step: TestStep,
    withAlt: (altSelector: string) => Promise<void>,
  ): Promise<void> {
    try {
      await primary();
    } catch (originalErr) {
      const mutation = this.mutator.mutateSelector(step);
      if (!mutation) throw originalErr;

      for (const alt of mutation.candidateSelectors) {
        try {
          await withAlt(alt);
          console.log(`[QAAgent] Self-healed: ${mutation.originalSelector} → ${alt}`);
          return;
        } catch {
          // try next candidate
        }
      }

      throw originalErr;
    }
  }

  private async bodyText(page: Page): Promise<string> {
    return page.locator('body').innerText({ timeout: 1000 }).then((text) => text.slice(0, 5000)).catch(() => '');
  }
}
