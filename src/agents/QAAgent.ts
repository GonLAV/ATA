import { v4 as uuidv4 } from 'uuid';
import type { Page, BrowserContext, Request, Response } from 'playwright';

import { BrowserManager } from '../browser/BrowserManager';
import { PageExplorer } from '../browser/PageExplorer';
import { ScreenshotManager } from '../browser/ScreenshotManager';
import { LLMClient } from './LLMClient';
import {
  createRun,
  updateRun,
  createScenario,
  updateScenario,
  createBugReport,
  getBugsByRun,
  getScenariosByRun,
} from '../database/Repository';
import { buildDashboard } from '../reporters/BugReporter';
import type {
  TestRun,
  TestScenario,
  TestStep,
  BugReport,
  Dashboard,
  PageSnapshot,
} from '../types';

const STEP_TIMEOUT_MS = 10_000;

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

  constructor() {
    this.browserManager = new BrowserManager();
    this.screenshotManager = new ScreenshotManager();
    this.explorer = new PageExplorer(this.screenshotManager);
    this.llm = new LLMClient();
  }

  /**
   * Run a full QA session for the given URL.
   * Returns the run ID so callers can poll for results asynchronously.
   */
  async startRun(url: string): Promise<string> {
    const runId = uuidv4();
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

    // Execute asynchronously so the HTTP response returns immediately
    this.execute(runId, url).catch((err) => {
      console.error(`[QAAgent] Run ${runId} crashed:`, err);
      updateRun(runId, { status: 'failed', completedAt: new Date().toISOString() });
    });

    return runId;
  }

  /**
   * Block until the run is complete and return the dashboard.
   * Useful for CLI / direct invocation.
   */
  async runSync(url: string): Promise<Dashboard> {
    const runId = await this.startRun(url);
    return this.execute(runId, url);
  }

  // ─── Core execution pipeline ─────────────────────────────────────────────

  private async execute(runId: string, url: string): Promise<Dashboard> {
    updateRun(runId, { status: 'running' });
    console.log(`[QAAgent] Starting run ${runId} for ${url}`);

    try {
      await this.browserManager.launch();

      // ── Step 1: Explore the page ──
      const exploreContext = await this.browserManager.newContext();
      let snapshot: PageSnapshot;
      try {
        snapshot = await this.explorer.explore(exploreContext, url);
      } finally {
        await exploreContext.close();
      }

      console.log(
        `[QAAgent] Snapshot: ${snapshot.buttons.length} buttons, ${snapshot.forms.length} forms, ${snapshot.links.length} links`,
      );

      // Log any immediately visible issues from the initial page load
      if (snapshot.consoleErrors.length > 0) {
        console.warn(`[QAAgent] Console errors on initial load:`, snapshot.consoleErrors);
      }

      // ── Step 2: Generate test scenarios ──
      const rawScenarios = await this.llm.generateTestScenarios(snapshot);
      console.log(`[QAAgent] LLM generated ${rawScenarios.length} scenarios`);

      const scenarios: TestScenario[] = rawScenarios.map((s) => ({
        ...s,
        id: uuidv4(),
        runId,
        status: 'pending',
      }));

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
        const result = await this.executeScenario(runId, url, scenario);

        if (result.bug) {
          createBugReport(result.bug);
          bugsFound++;
          updateRun(runId, { bugsFound });
        }

        if (result.passed) {
          passed++;
        } else {
          failed++;
        }

        updateRun(runId, { passedScenarios: passed, failedScenarios: failed });
      }

      // ── Step 4: Finalise ──
      const completedAt = new Date().toISOString();
      updateRun(runId, { status: 'completed', completedAt });

      const finalRun: TestRun = {
        id: runId,
        url,
        status: 'completed',
        startedAt: snapshot.url, // will be replaced below from DB
        completedAt,
        totalScenarios: scenarios.length,
        passedScenarios: passed,
        failedScenarios: failed,
        bugsFound,
      };

      const allScenarios = getScenariosByRun(runId);
      const allBugs = getBugsByRun(runId);
      return buildDashboard(finalRun, allScenarios, allBugs);
    } finally {
      await this.browserManager.close();
    }
  }

  // ─── Scenario executor ────────────────────────────────────────────────────

  private async executeScenario(
    runId: string,
    baseUrl: string,
    scenario: TestScenario,
  ): Promise<{ passed: boolean; bug?: BugReport }> {
    const context = await this.browserManager.newContext();
    const page = await context.newPage();

    const consoleErrors: string[] = [];
    const networkErrors: string[] = [];
    const executedSteps: string[] = [];

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
      await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: STEP_TIMEOUT_MS });
      executedSteps.push(`Navigate to ${baseUrl}`);

      for (const step of scenario.steps) {
        await this.executeStep(page, step, baseUrl);
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

    if (!hasError) {
      return { passed: true };
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
      id: uuidv4(),
      runId,
      scenarioId: scenario.id,
      detectedAt: new Date().toISOString(),
      screenshotPath,
      ...bugDetails,
    };

    return { passed: false, bug };
  }

  // ─── Step executor ────────────────────────────────────────────────────────

  private async executeStep(page: Page, step: TestStep, baseUrl: string): Promise<void> {
    const timeout = STEP_TIMEOUT_MS;

    switch (step.action) {
      case 'navigate':
        await page.goto(step.value ?? baseUrl, { waitUntil: 'domcontentloaded', timeout });
        break;

      case 'click':
        if (step.selector) {
          await page.waitForSelector(step.selector, { timeout }).catch(() => undefined);
          await page.click(step.selector, { timeout });
        }
        break;

      case 'fill':
        if (step.selector && step.value !== undefined) {
          await page.waitForSelector(step.selector, { timeout }).catch(() => undefined);
          await page.fill(step.selector, step.value, { timeout });
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
  }
}
