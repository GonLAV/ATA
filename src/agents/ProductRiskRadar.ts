import { randomUUID } from 'crypto';
import type {
  ActionObservation,
  PageSnapshot,
  ProductRiskSignal,
  Severity,
  TestScenario,
} from '../types';

interface SignalInput {
  runId: string;
  scenarioId?: string;
  type: ProductRiskSignal['type'];
  title: string;
  severity: Severity;
  evidence: string[];
  recommendation: string;
  url: string;
}

/**
 * Product Risk Radar turns low-level browser evidence into product-facing QA
 * signals. It catches silent failures that do not always throw exceptions:
 * dead clicks, inaccessible forms, missing next actions, and brittle funnels.
 */
export class ProductRiskRadar {
  analyzeSnapshot(runId: string, snapshot: PageSnapshot): ProductRiskSignal[] {
    const signals: ProductRiskSignal[] = [];
    const url = snapshot.finalUrl ?? snapshot.url;

    if (snapshot.consoleErrors.length > 0 || snapshot.networkErrors.length > 0) {
      signals.push(this.createSignal({
        runId,
        type: 'technical_reliability',
        title: 'Initial page load produced observable runtime or network failures',
        severity: snapshot.networkErrors.some((error) => error.includes('HTTP 5')) ? 'high' : 'medium',
        evidence: [
          ...snapshot.consoleErrors.slice(0, 5).map((error) => `Console: ${error}`),
          ...snapshot.networkErrors.slice(0, 5).map((error) => `Network: ${error}`),
        ],
        recommendation: 'Fix load-time errors before relying on downstream flow results; they can mask product defects and create flaky CI runs.',
        url,
      }));
    }

    const unlabeledInputs = snapshot.inputs.filter((input) => {
      const inputType = input.type?.toLowerCase();
      return inputType !== 'submit' && !input.ariaLabel && !input.placeholder && !input.name && !input.id;
    });

    if (unlabeledInputs.length > 0) {
      signals.push(this.createSignal({
        runId,
        type: 'accessibility_gap',
        title: 'Interactive form fields lack machine-readable labels',
        severity: 'medium',
        evidence: unlabeledInputs.slice(0, 5).map((input) => `${input.tag}${input.type ? `[type=${input.type}]` : ''} at ${input.selector}`),
        recommendation: 'Add labels, aria-label attributes, stable names, or placeholders so assistive tech and automated QA can understand each field.',
        url,
      }));
    }

    const hasCredentials = snapshot.inputs.some((input) => input.type === 'password' || /email|user|login/i.test(`${input.name ?? ''} ${input.placeholder ?? ''}`));
    if (hasCredentials && snapshot.buttons.length === 0 && snapshot.forms.every((form) => !form.submitButton)) {
      signals.push(this.createSignal({
        runId,
        type: 'conversion_friction',
        title: 'Authentication-like form has no obvious submit action',
        severity: 'high',
        evidence: ['Credential-related fields were found, but no button or form submit control was discoverable.'],
        recommendation: 'Expose a visible, accessible submit button with a stable selector and clear text such as Sign in, Continue, or Create account.',
        url,
      }));
    }

    if (snapshot.buttons.length === 0 && snapshot.forms.length === 0 && snapshot.links.length === 0) {
      signals.push(this.createSignal({
        runId,
        type: 'coverage_gap',
        title: 'Page has no discoverable interactive path for autonomous QA',
        severity: 'medium',
        evidence: ['No buttons, forms, or links were visible in the initial viewport snapshot.'],
        recommendation: 'Confirm the page is not blocked by a loading state, consent modal, auth wall, or inaccessible custom controls.',
        url,
      }));
    }

    const riskyLinks = snapshot.links.filter((link) => {
      const href = link.href ?? '';
      return href === '' || href === '#' || href.startsWith('javascript:');
    });

    if (riskyLinks.length > 0) {
      signals.push(this.createSignal({
        runId,
        type: 'navigation_risk',
        title: 'Navigation contains non-destination links',
        severity: 'low',
        evidence: riskyLinks.slice(0, 5).map((link) => `${link.text ?? link.selector} -> ${link.href ?? '(empty href)'}`),
        recommendation: 'Replace placeholder links with real destinations or buttons so users and automation get predictable navigation behavior.',
        url,
      }));
    }

    return signals;
  }

  analyzeScenario(
    runId: string,
    scenario: TestScenario,
    observations: ActionObservation[],
  ): ProductRiskSignal[] {
    const signals: ProductRiskSignal[] = [];
    const clickObservations = observations.filter((observation) => observation.action === 'click' || observation.action === 'submit');

    for (const observation of clickObservations) {
      const noVisibleEffect = !observation.urlChanged && !observation.domChanged && observation.networkActivityDelta === 0 && observation.networkErrorsDelta === 0 && observation.consoleErrorsDelta === 0;
      if (!noVisibleEffect) continue;

      signals.push(this.createSignal({
        runId,
        scenarioId: scenario.id,
        type: 'dead_interaction',
        title: `Interaction produced no observable result: ${observation.stepDescription}`,
        severity: scenario.priority === 'high' ? 'high' : 'medium',
        evidence: [
          `Action: ${observation.action}${observation.selector ? ` on ${observation.selector}` : ''}`,
          `URL stayed on ${observation.afterUrl}`,
          'Page text snapshot did not change after the interaction.',
          'No network activity, console error, or network failure was emitted during the step.',
        ],
        recommendation: 'Verify whether this control should navigate, mutate UI state, submit data, or show validation. Add visible feedback for intentional no-op states.',
        url: observation.afterUrl,
      }));
    }

    return signals;
  }

  score(signals: ProductRiskSignal[]): number {
    if (signals.length === 0) return 100;

    const penalty = signals.reduce((total, signal) => {
      switch (signal.severity) {
        case 'critical': return total + 35;
        case 'high': return total + 22;
        case 'medium': return total + 12;
        case 'low': return total + 5;
        default: return total;
      }
    }, 0);

    return Math.max(0, 100 - penalty);
  }

  private createSignal(input: SignalInput): ProductRiskSignal {
    return {
      id: randomUUID(),
      detectedAt: new Date().toISOString(),
      ...input,
    };
  }
}