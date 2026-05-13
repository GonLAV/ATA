import type { TestStep } from '../types';

export type MutationStrategy = 'selector_alt' | 'timing_cushion';

export interface SelectorMutation {
  strategy: 'selector_alt';
  originalSelector: string;
  candidateSelectors: string[];
}

/**
 * Generates alternative ways to target elements when a selector fails,
 * and can inject timing cushions before flaky steps.
 */
export class TestMutator {
  /**
   * Given a step that failed due to a selector error, produce up to 4
   * alternative selectors ranked by likelihood of matching.
   * Returns null when no useful alternatives can be generated.
   */
  mutateSelector(step: TestStep): SelectorMutation | null {
    if (!step.selector) return null;

    const sel = step.selector;
    const candidates: string[] = [];

    // ID → attribute-based (survives framework-generated id prefixes)
    if (sel.startsWith('#')) {
      const id = sel.slice(1);
      candidates.push(`[id="${id}"]`);
      candidates.push(`[id*="${id}"]`);
    }

    // Class → partial-match class
    if (sel.startsWith('.')) {
      const cls = sel.slice(1);
      candidates.push(`[class*="${cls}"]`);
    }

    // Button / submit variants
    if (/^button|input\[type.*submit/i.test(sel)) {
      candidates.push('[role="button"]');
      candidates.push('button[type="submit"]');
      candidates.push('input[type="submit"]');
    }

    // Text from the step description (quoted phrases)
    const textMatch = step.description.match(/["']([^"']{2,40})["']/);
    if (textMatch) {
      const txt = textMatch[1];
      candidates.push(`:text("${txt}")`);
      candidates.push(`[aria-label*="${txt}"]`);
      candidates.push(`[placeholder*="${txt}"]`);
    }

    // Input/textarea fallbacks
    if (/input|textarea/i.test(sel)) {
      candidates.push('form input:not([type="hidden"]):nth-of-type(1)');
      candidates.push('input:visible');
    }

    // Deduplicate, remove the original, cap at 4
    const unique = [...new Set(candidates)].filter((c) => c !== sel).slice(0, 4);
    if (unique.length === 0) return null;

    return { strategy: 'selector_alt', originalSelector: sel, candidateSelectors: unique };
  }

  /**
   * Returns a copy of the steps list with a wait injected immediately
   * before the step at failedIndex. Helps when elements render asynchronously.
   */
  addTimingCushion(steps: TestStep[], failedIndex: number, waitMs = 2000): TestStep[] {
    const mutated = [...steps];
    const waitStep: TestStep = {
      action: 'wait',
      value: String(waitMs),
      description: `Wait ${waitMs}ms for element availability (self-healing)`,
    };
    mutated.splice(failedIndex, 0, waitStep);
    return mutated;
  }
}
