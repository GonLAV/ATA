/**
 * Shared TypeScript types for QA Copilot.
 */

// ─── Test Run ───────────────────────────────────────────────────────────────

export type RunStatus = 'pending' | 'running' | 'completed' | 'failed';

export interface TestRun {
  id: string;
  url: string;
  status: RunStatus;
  startedAt: string;    // ISO-8601
  completedAt?: string; // ISO-8601
  totalScenarios: number;
  passedScenarios: number;
  failedScenarios: number;
  bugsFound: number;
}

// ─── Page Snapshot ──────────────────────────────────────────────────────────

export interface InteractiveElement {
  tag: string;
  type?: string;       // for <input>
  text?: string;
  placeholder?: string;
  href?: string;
  id?: string;
  name?: string;
  ariaLabel?: string;
  selector: string;    // CSS selector to target the element
}

export interface PageSnapshot {
  url: string;
  finalUrl?: string;
  title: string;
  forms: FormInfo[];
  inputs: InteractiveElement[];
  buttons: InteractiveElement[];
  links: InteractiveElement[];
  headings?: string[];
  consoleErrors: string[];
  networkErrors: string[];
  screenshotPath?: string;
}

export interface ExplorationMap {
  entryUrl: string;
  snapshots: PageSnapshot[];
  routes: string[];
  discoveredAt: string;
}

export interface FormInfo {
  id?: string;
  action?: string;
  method?: string;
  inputs: InteractiveElement[];
  submitButton?: InteractiveElement;
}

// ─── Test Scenario ───────────────────────────────────────────────────────────

export type ActionType =
  | 'navigate'
  | 'click'
  | 'fill'
  | 'submit'
  | 'wait'
  | 'screenshot'
  | 'assert_visible'
  | 'assert_text'
  | 'assert_url';

export interface TestStep {
  action: ActionType;
  selector?: string;
  value?: string;       // text to fill / URL to assert
  description: string;  // human-readable label
}

export interface ActionObservation {
  stepDescription: string;
  action: ActionType;
  selector?: string;
  beforeUrl: string;
  afterUrl: string;
  urlChanged: boolean;
  domChanged: boolean;
  networkActivityDelta: number;
  networkErrorsDelta: number;
  consoleErrorsDelta: number;
  durationMs: number;
}

export interface TestScenario {
  id: string;
  runId: string;
  title: string;
  description: string;
  priority: 'high' | 'medium' | 'low';
  steps: TestStep[];
  status: 'pending' | 'passed' | 'failed' | 'skipped';
  durationMs?: number;
}

// ─── Bug Report ───────────────────────────────────────────────────────────────

export type Severity = 'critical' | 'high' | 'medium' | 'low';

export interface BugReport {
  id: string;
  runId: string;
  scenarioId?: string;
  title: string;
  severity: Severity;
  description: string;
  reproductionSteps: string[];
  expectedBehavior: string;
  actualBehavior: string;
  screenshotPath?: string;
  url: string;
  detectedAt: string; // ISO-8601
  // Raw evidence
  consoleErrors?: string[];
  networkErrors?: string[];
  errorStack?: string;
}

// ─── Product Risk Radar ─────────────────────────────────────────────────────

export type RiskSignalType =
  | 'dead_interaction'
  | 'accessibility_gap'
  | 'conversion_friction'
  | 'navigation_risk'
  | 'technical_reliability'
  | 'coverage_gap';

export interface ProductRiskSignal {
  id: string;
  runId: string;
  scenarioId?: string;
  type: RiskSignalType;
  title: string;
  severity: Severity;
  evidence: string[];
  recommendation: string;
  url: string;
  detectedAt: string;
}

// ─── Dashboard ────────────────────────────────────────────────────────────────

export interface SeverityCounts {
  critical: number;
  high: number;
  medium: number;
  low: number;
}

export interface Dashboard {
  runId: string;
  url: string;
  status: RunStatus;
  startedAt: string;
  completedAt?: string;
  durationMs?: number;
  totalScenarios: number;
  passedScenarios: number;
  failedScenarios: number;
  skippedScenarios: number;
  bugsFound: number;
  severityCounts: SeverityCounts;
  riskScore: number;
  bugs: BugReport[];
  scenarios: TestScenario[];
  productRiskSignals: ProductRiskSignal[];
  exploratoryMap?: ExplorationMap;
  coverageAreas: string[];
}

// ─── Regression Contracts ──────────────────────────────────────────────────

export interface RegressionContract {
  runId: string;
  url: string;
  generatedAt: string;
  framework: 'playwright';
  testCount: number;
  sourceIssueCount: number;
  sourceRiskCount: number;
  specFilename: string;
  spec: string;
}

// ─── API ──────────────────────────────────────────────────────────────────────

export interface CreateRunRequest {
  url: string;
}

export interface CreateRunResponse {
  runId: string;
  message: string;
}
