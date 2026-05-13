import type { BugReport, ProductRiskSignal } from '../types';

export type TrustLevel = 'high' | 'medium' | 'low' | 'unreliable';
export type EvaluationRecommendation = 'accept' | 'review' | 'reject';

export interface EvaluationLayer {
  name: string;
  passed: boolean;
  score: number; // 0–1
  details: string;
}

export interface EvaluationResult {
  targetId: string;
  targetType: 'bug' | 'risk';
  layers: EvaluationLayer[];
  overallScore: number;    // weighted layer average, 0–1
  confidenceScore: number; // Bayesian posterior, 0–1
  trustLevel: TrustLevel;
  recommendation: EvaluationRecommendation;
  evaluatedAt: string;
}

// Layer contribution weights must sum to 1.
const WEIGHTS: Record<string, number> = {
  syntax:        0.20,
  semantic:      0.25,
  factual:       0.30,
  reasoning:     0.15,
  contradiction: 0.10,
};

export class EvaluationEngine {
  evaluate(bug: BugReport): EvaluationResult {
    const layers: EvaluationLayer[] = [
      this.syntaxLayer(bug),
      this.semanticLayer(bug),
      this.factualLayer(bug),
      this.reasoningLayer(bug),
      this.contradictionLayer(bug),
    ];

    const overallScore = layers.reduce(
      (acc, l) => acc + l.score * (WEIGHTS[l.name] ?? 0.2),
      0,
    );

    const confidenceScore = this.bayesianConfidence(bug);

    return {
      targetId: bug.id,
      targetType: 'bug',
      layers,
      overallScore: r2(overallScore),
      confidenceScore: r2(confidenceScore),
      trustLevel: this.toTrustLevel(confidenceScore),
      recommendation: this.toRecommendation(overallScore, confidenceScore),
      evaluatedAt: new Date().toISOString(),
    };
  }

  evaluateRisk(risk: ProductRiskSignal): EvaluationResult {
    const hasEvidence = risk.evidence.length > 0;
    const hasRecommendation = risk.recommendation.length > 10;
    const score = (hasEvidence ? 0.6 : 0) + (hasRecommendation ? 0.4 : 0);

    const confidenceScore = this.bayesianRiskConfidence(risk);

    return {
      targetId: risk.id,
      targetType: 'risk',
      layers: [
        {
          name: 'factual',
          passed: hasEvidence,
          score,
          details: hasEvidence
            ? `${risk.evidence.length} evidence item(s) recorded`
            : 'No supporting evidence',
        },
      ],
      overallScore: r2(score),
      confidenceScore: r2(confidenceScore),
      trustLevel: this.toTrustLevel(confidenceScore),
      recommendation: this.toRecommendation(score, confidenceScore),
      evaluatedAt: new Date().toISOString(),
    };
  }

  // ─── Layers ───────────────────────────────────────────────────────────────

  private syntaxLayer(bug: BugReport): EvaluationLayer {
    const checks = [
      { field: 'title', ok: bug.title.length > 5 && bug.title !== 'Unknown issue' },
      { field: 'description', ok: bug.description.length > 20 },
      { field: 'reproductionSteps', ok: bug.reproductionSteps.length >= 1 },
      { field: 'expectedBehavior', ok: bug.expectedBehavior.length > 10 },
      { field: 'actualBehavior', ok: bug.actualBehavior.length > 10 },
      { field: 'url', ok: isValidUrl(bug.url) },
    ];

    const failed = checks.filter((c) => !c.ok).map((c) => c.field);
    const score = (checks.length - failed.length) / checks.length;

    return {
      name: 'syntax',
      passed: score >= 0.8,
      score,
      details: failed.length === 0
        ? 'All required fields are complete'
        : `Incomplete or missing: ${failed.join(', ')}`,
    };
  }

  private semanticLayer(bug: BugReport): EvaluationLayer {
    const checks = [
      {
        field: 'non-generic title',
        ok: !/unknown|undefined|null|error occurred/i.test(bug.title),
      },
      {
        field: 'substantive description',
        ok: bug.description.split(/\s+/).length >= 8,
      },
      // Critical severity must mention impact keywords
      {
        field: 'severity-description alignment',
        ok:
          bug.severity !== 'critical' ||
          /crash|auth|bypass|data loss|unavailable|broken|inaccessible/i.test(bug.description),
      },
      {
        field: 'specific actual behavior',
        ok: bug.actualBehavior.length > 20 && bug.actualBehavior !== bug.description,
      },
    ];

    const failed = checks.filter((c) => !c.ok).map((c) => c.field);
    const score = (checks.length - failed.length) / checks.length;

    return {
      name: 'semantic',
      passed: score >= 0.75,
      score,
      details: failed.length === 0
        ? 'Bug report is semantically coherent'
        : `Semantic issues: ${failed.join('; ')}`,
    };
  }

  private factualLayer(bug: BugReport): EvaluationLayer {
    const hasConsoleErrors = (bug.consoleErrors?.length ?? 0) > 0;
    const hasNetworkErrors = (bug.networkErrors?.length ?? 0) > 0;
    const hasScreenshot = Boolean(bug.screenshotPath);
    const hasErrorStack = Boolean(bug.errorStack);

    const score = [
      hasConsoleErrors ? 0.30 : 0,
      hasNetworkErrors ? 0.25 : 0,
      hasScreenshot    ? 0.25 : 0,
      hasErrorStack    ? 0.20 : 0,
    ].reduce((a, b) => a + b, 0);

    const present = [
      hasConsoleErrors && 'console errors',
      hasNetworkErrors && 'network errors',
      hasScreenshot    && 'screenshot',
      hasErrorStack    && 'error stack',
    ].filter(Boolean);

    return {
      name: 'factual',
      passed: score >= 0.4,
      score,
      details: present.length > 0
        ? `Evidence present: ${present.join(', ')}`
        : 'No supporting evidence attached',
    };
  }

  private reasoningLayer(bug: BugReport): EvaluationLayer {
    const steps = bug.reproductionSteps;

    const checks = [
      {
        field: 'navigation step',
        ok: steps.some((s) => /navigate|open|go to|visit|http/i.test(s)),
      },
      {
        field: 'specific actions',
        ok: steps.some((s) => /click|fill|type|submit|enter|select/i.test(s)),
      },
      { field: 'step count ≥ 2', ok: steps.length >= 2 },
    ];

    const failed = checks.filter((c) => !c.ok).map((c) => c.field);
    const score = (checks.length - failed.length) / checks.length;

    return {
      name: 'reasoning',
      passed: score >= 0.67,
      score,
      details: failed.length === 0
        ? 'Reproduction steps are logically structured'
        : `Reasoning gaps: ${failed.join(', ')}`,
    };
  }

  private contradictionLayer(bug: BugReport): EvaluationLayer {
    const expected = bug.expectedBehavior.toLowerCase().trim();
    const actual = bug.actualBehavior.toLowerCase().trim();
    const areDifferent = expected !== actual;
    const similarity = jaccardSimilarity(expected, actual);
    const score = areDifferent ? Math.max(0, 1 - Math.max(0, similarity - 0.5) * 2) : 0;

    return {
      name: 'contradiction',
      passed: areDifferent && similarity < 0.7,
      score: Math.max(0, score),
      details: !areDifferent
        ? 'Expected and actual behaviors are identical'
        : similarity > 0.7
          ? `Expected and actual are highly similar (${(similarity * 100).toFixed(0)}% token overlap)`
          : 'Expected and actual behaviors are genuinely distinct',
    };
  }

  // ─── Bayesian confidence ──────────────────────────────────────────────────
  //
  // We model this as a Naive Bayes update in log-odds space:
  //   log_odds_posterior = log_odds_prior + Σ log(LR_i)
  // where LR_i = P(evidence_i | genuine_bug) / P(evidence_i | false_positive)
  //
  // Prior: P(genuine) = 0.60  →  log_odds ≈ 0.405

  private bayesianConfidence(bug: BugReport): number {
    const PRIOR = 0.6;
    let logOdds = Math.log(PRIOR / (1 - PRIOR));

    if ((bug.consoleErrors?.length ?? 0) > 0) logOdds += Math.log(0.85 / 0.35);
    if ((bug.networkErrors?.length ?? 0) > 0)  logOdds += Math.log(0.80 / 0.30);
    if (bug.screenshotPath)                     logOdds += Math.log(0.90 / 0.50);
    if (bug.errorStack)                         logOdds += Math.log(0.95 / 0.20);
    if (bug.reproductionSteps.length > 3)       logOdds += Math.log(1.30 / 1.00);

    // Suspicious: critical severity with zero supporting errors
    if (
      bug.severity === 'critical' &&
      !(bug.consoleErrors?.length) &&
      !(bug.networkErrors?.length) &&
      !bug.errorStack
    ) {
      logOdds += Math.log(0.30 / 0.70);
    }

    if (/unknown|undefined|null/i.test(bug.title)) {
      logOdds += Math.log(0.50 / 1.00);
    }

    return sigmoid(logOdds);
  }

  private bayesianRiskConfidence(risk: ProductRiskSignal): number {
    const PRIOR = 0.55;
    let logOdds = Math.log(PRIOR / (1 - PRIOR));

    if (risk.evidence.length >= 2)                        logOdds += Math.log(1.80 / 1.00);
    if (risk.severity === 'critical' || risk.severity === 'high') logOdds += Math.log(1.40 / 1.00);
    if (risk.recommendation.length > 30)                  logOdds += Math.log(1.20 / 1.00);

    return sigmoid(logOdds);
  }

  // ─── Helpers ──────────────────────────────────────────────────────────────

  private toTrustLevel(confidence: number): TrustLevel {
    if (confidence >= 0.75) return 'high';
    if (confidence >= 0.55) return 'medium';
    if (confidence >= 0.35) return 'low';
    return 'unreliable';
  }

  private toRecommendation(score: number, confidence: number): EvaluationRecommendation {
    if (score >= 0.70 && confidence >= 0.65) return 'accept';
    if (score < 0.40 || confidence < 0.35)  return 'reject';
    return 'review';
  }
}

function sigmoid(x: number): number {
  return 1 / (1 + Math.exp(-x));
}

function r2(n: number): number {
  return Math.round(n * 100) / 100;
}

function isValidUrl(url: string): boolean {
  try { new URL(url); return true; } catch { return false; }
}

function jaccardSimilarity(a: string, b: string): number {
  const setA = new Set(a.split(/\s+/));
  const setB = new Set(b.split(/\s+/));
  const intersection = [...setA].filter((w) => setB.has(w)).length;
  const union = new Set([...setA, ...setB]).size;
  return union === 0 ? 1 : intersection / union;
}
