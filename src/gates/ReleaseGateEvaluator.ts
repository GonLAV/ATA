import { getConfig } from '../config/Config';
import { ProductRiskRadar } from '../agents/ProductRiskRadar';
import type {
  BugReport,
  ProductRiskSignal,
  ReleaseGate,
  ReleaseGateCheck,
  ReleaseGateDecision,
  ReleaseGateThresholds,
  TestRun,
  TestScenario,
} from '../types';

/**
 * Autonomous Release Gate converts QA evidence into a CI/CD decision. It is
 * deliberately deterministic so teams can trust and audit every ship/hold call.
 */
export class ReleaseGateEvaluator {
  private readonly thresholds: ReleaseGateThresholds;

  constructor(thresholds?: Partial<ReleaseGateThresholds>) {
    const config = getConfig();
    this.thresholds = {
      minRiskScore: config.releaseGateMinRiskScore,
      maxHighSeverityBugs: config.releaseGateMaxHighSeverityBugs,
      maxHighSeverityRisks: config.releaseGateMaxHighSeverityRisks,
      maxFailedScenarioRate: config.releaseGateMaxFailedScenarioRate,
      ...thresholds,
    };
  }

  evaluate(
    run: TestRun,
    scenarios: TestScenario[],
    bugs: BugReport[],
    risks: ProductRiskSignal[],
  ): ReleaseGate {
    const riskScore = new ProductRiskRadar().score(risks);
    const highSeverityBugCount = bugs.filter((bug) => bug.severity === 'critical' || bug.severity === 'high').length;
    const highSeverityRiskCount = risks.filter((risk) => risk.severity === 'critical' || risk.severity === 'high').length;
    const failedScenarioRate = scenarios.length > 0
      ? scenarios.filter((scenario) => scenario.status === 'failed').length / scenarios.length
      : 0;

    const checks: ReleaseGateCheck[] = [
      this.checkRiskScore(riskScore),
      this.checkHighSeverityBugs(highSeverityBugCount),
      this.checkHighSeverityRisks(highSeverityRiskCount),
      this.checkFailedScenarioRate(failedScenarioRate, scenarios.length),
      this.checkRunStatus(run.status),
    ];

    const decision = this.decisionFor(checks);
    const requiredActions = this.requiredActions(checks, bugs, risks);

    return {
      runId: run.id,
      url: run.url,
      evaluatedAt: new Date().toISOString(),
      decision,
      ciExitCode: decision === 'block' ? 1 : 0,
      confidence: this.confidenceFor(checks, scenarios.length, bugs.length + risks.length),
      summary: this.summaryFor(decision, checks),
      thresholds: this.thresholds,
      checks,
      requiredActions,
    };
  }

  private checkRiskScore(riskScore: number): ReleaseGateCheck {
    return {
      name: 'Product risk score',
      passed: riskScore >= this.thresholds.minRiskScore,
      severity: riskScore >= this.thresholds.minRiskScore ? 'info' : 'blocking',
      observed: `${riskScore}/100`,
      threshold: `>= ${this.thresholds.minRiskScore}/100`,
      recommendation: 'Resolve high-impact risk signals or explicitly lower the release threshold for this environment.',
    };
  }

  private checkHighSeverityBugs(count: number): ReleaseGateCheck {
    return {
      name: 'Critical/high bugs',
      passed: count <= this.thresholds.maxHighSeverityBugs,
      severity: count <= this.thresholds.maxHighSeverityBugs ? 'info' : 'blocking',
      observed: `${count}`,
      threshold: `<= ${this.thresholds.maxHighSeverityBugs}`,
      recommendation: 'Fix or waive critical/high bugs before promoting this release.',
    };
  }

  private checkHighSeverityRisks(count: number): ReleaseGateCheck {
    return {
      name: 'Critical/high product risks',
      passed: count <= this.thresholds.maxHighSeverityRisks,
      severity: count <= this.thresholds.maxHighSeverityRisks ? 'info' : 'warning',
      observed: `${count}`,
      threshold: `<= ${this.thresholds.maxHighSeverityRisks}`,
      recommendation: 'Review high-severity product risks and convert accepted risks into tracked release notes.',
    };
  }

  private checkFailedScenarioRate(rate: number, scenarioCount: number): ReleaseGateCheck {
    const percentage = Math.round(rate * 100);
    const threshold = Math.round(this.thresholds.maxFailedScenarioRate * 100);
    return {
      name: 'Failed scenario rate',
      passed: rate <= this.thresholds.maxFailedScenarioRate,
      severity: rate <= this.thresholds.maxFailedScenarioRate ? 'info' : 'blocking',
      observed: `${percentage}% across ${scenarioCount} scenarios`,
      threshold: `<= ${threshold}%`,
      recommendation: 'Reduce failed scenarios or split flaky exploratory findings from release-blocking flows.',
    };
  }

  private checkRunStatus(status: TestRun['status']): ReleaseGateCheck {
    return {
      name: 'Run completed',
      passed: status === 'completed',
      severity: status === 'completed' ? 'info' : 'blocking',
      observed: status,
      threshold: 'completed',
      recommendation: 'Only evaluate releases from completed QA Copilot runs.',
    };
  }

  private decisionFor(checks: ReleaseGateCheck[]): ReleaseGateDecision {
    if (checks.some((check) => !check.passed && check.severity === 'blocking')) return 'block';
    if (checks.some((check) => !check.passed && check.severity === 'warning')) return 'warn';
    return 'ship';
  }

  private confidenceFor(checks: ReleaseGateCheck[], scenarioCount: number, findingCount: number): number {
    const passedRatio = checks.filter((check) => check.passed).length / checks.length;
    const scenarioCoverageBonus = Math.min(20, scenarioCount * 4);
    const findingPenalty = Math.min(25, findingCount * 3);
    return Math.max(0, Math.min(100, Math.round(passedRatio * 80 + scenarioCoverageBonus - findingPenalty)));
  }

  private summaryFor(decision: ReleaseGateDecision, checks: ReleaseGateCheck[]): string {
    const failingChecks = checks.filter((check) => !check.passed).map((check) => check.name);
    if (decision === 'ship') return 'Release is clear to ship based on current autonomous QA evidence.';
    if (decision === 'warn') return `Release can proceed with review: ${failingChecks.join(', ')}.`;
    return `Release should be blocked: ${failingChecks.join(', ')}.`;
  }

  private requiredActions(checks: ReleaseGateCheck[], bugs: BugReport[], risks: ProductRiskSignal[]): string[] {
    const failedActions = checks
      .filter((check) => !check.passed)
      .map((check) => check.recommendation);

    const topBugs = bugs
      .filter((bug) => bug.severity === 'critical' || bug.severity === 'high')
      .slice(0, 3)
      .map((bug) => `Fix bug: ${bug.title}`);

    const topRisks = risks
      .filter((risk) => risk.severity === 'critical' || risk.severity === 'high')
      .slice(0, 3)
      .map((risk) => `Review risk: ${risk.title}`);

    return Array.from(new Set([...failedActions, ...topBugs, ...topRisks]));
  }
}