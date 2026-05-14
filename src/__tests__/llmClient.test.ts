/**
 * Tests for LLMClient — heuristic scenario generation and output validation
 * exercised without an API key (no real LLM calls).
 */
import { LLMClient } from '../agents/LLMClient';
import { resetConfigForTests } from '../config/Config';
import type { PageSnapshot } from '../types';

function makeSnapshot(overrides: Partial<PageSnapshot> = {}): PageSnapshot {
  return {
    url: 'https://example.com',
    finalUrl: 'https://example.com',
    title: 'Example App',
    forms: [],
    buttons: [],
    links: [],
    inputs: [],
    consoleErrors: [],
    networkErrors: [],
    ...overrides,
  };
}

beforeEach(() => {
  resetConfigForTests();
  // No API key → heuristic path
  delete process.env.OPENAI_API_KEY;
  delete process.env.OPENAI_MODEL;
});

afterEach(() => {
  resetConfigForTests();
});

describe('LLMClient — heuristic scenario generation (no API key)', () => {
  test('returns navigation scenario when links are present', async () => {
    const client = new LLMClient();
    const snapshot = makeSnapshot({
      links: [{ href: 'https://example.com/about', text: 'About', selector: 'a[href="/about"]' }],
    });

    const scenarios = await client.generateTestScenarios(snapshot);

    expect(scenarios.length).toBeGreaterThan(0);
    const nav = scenarios.find((s) => s.title.toLowerCase().includes('navigation'));
    expect(nav).toBeDefined();
    expect(nav!.steps.some((step) => step.action === 'navigate')).toBe(true);
    expect(nav!.steps.some((step) => step.action === 'click')).toBe(true);
  });

  test('returns form scenario when fillable inputs are present', async () => {
    const client = new LLMClient();
    const snapshot = makeSnapshot({
      forms: [
        {
          id: 'login-form',
          inputs: [
            { type: 'email', name: 'email', placeholder: 'Email', selector: '#email' },
            { type: 'password', name: 'password', placeholder: 'Password', selector: '#password' },
          ],
          submitButton: { selector: 'button[type=submit]', text: 'Login' },
        },
      ],
    });

    const scenarios = await client.generateTestScenarios(snapshot);

    const form = scenarios.find((s) => s.title.toLowerCase().includes('form'));
    expect(form).toBeDefined();
    expect(form!.priority).toBe('high'); // email/password → high priority
    expect(form!.steps.some((step) => step.action === 'fill')).toBe(true);
    expect(form!.steps.some((step) => step.action === 'submit')).toBe(true);
  });

  test('assigns high priority to auth-related buttons', async () => {
    const client = new LLMClient();
    const snapshot = makeSnapshot({
      buttons: [
        { text: 'Sign In', selector: 'button.sign-in', ariaLabel: undefined },
        { text: 'Read more', selector: 'button.read-more', ariaLabel: undefined },
      ],
    });

    const scenarios = await client.generateTestScenarios(snapshot);

    const signIn = scenarios.find((s) => s.title.includes('Sign In'));
    expect(signIn?.priority).toBe('high');

    const readMore = scenarios.find((s) => s.title.includes('Read more'));
    expect(readMore?.priority).toBe('medium');
  });

  test('caps output at 12 scenarios', async () => {
    const client = new LLMClient();
    const snapshot = makeSnapshot({
      buttons: Array.from({ length: 20 }, (_, i) => ({
        text: `Button ${i}`,
        selector: `.btn-${i}`,
        ariaLabel: undefined,
      })),
    });

    const scenarios = await client.generateTestScenarios(snapshot);
    expect(scenarios.length).toBeLessThanOrEqual(12);
  });

  test('skips links with mailto: href', async () => {
    const client = new LLMClient();
    const snapshot = makeSnapshot({
      links: [{ href: 'mailto:support@example.com', text: 'Email Us', selector: 'a[href^=mailto]' }],
    });

    const scenarios = await client.generateTestScenarios(snapshot);
    // mailto links should be skipped; navigation scenario may still appear but should use fallback
    const navStep = scenarios.flatMap((s) => s.steps).find((step) => step.value?.startsWith('mailto:'));
    expect(navStep).toBeUndefined();
  });

  test('all generated steps use valid action types', async () => {
    const validActions = new Set([
      'navigate', 'click', 'fill', 'submit', 'wait',
      'screenshot', 'assert_visible', 'assert_text', 'assert_url',
    ]);
    const client = new LLMClient();
    const snapshot = makeSnapshot({
      links: [{ href: 'https://example.com/contact', text: 'Contact', selector: 'a.contact' }],
      buttons: [{ text: 'Submit', selector: 'button.submit', ariaLabel: undefined }],
      forms: [
        {
          id: 'contact-form',
          inputs: [{ type: 'text', name: 'name', placeholder: 'Your name', selector: '#name' }],
          submitButton: { selector: 'button[type=submit]', text: 'Send' },
        },
      ],
    });

    const scenarios = await client.generateTestScenarios(snapshot);
    for (const scenario of scenarios) {
      for (const step of scenario.steps) {
        expect(validActions.has(step.action)).toBe(true);
      }
    }
  });
});

describe('LLMClient — classifyBug fallback (no API key)', () => {
  test('returns defaults when no API key is configured', async () => {
    const client = new LLMClient();
    const result = await client.classifyBug(
      'run-1', 'scenario-1',
      'https://example.com/login',
      'Button click had no effect',
      ['Open /login', 'Click Login'],
      ['TypeError: cannot read property of null'],
      [],
    );

    expect(result.title).toBe('Unknown issue');
    expect(result.severity).toBe('medium');
    expect(result.url).toBe('https://example.com/login');
    expect(result.description).toBe('Button click had no effect');
    expect(result.consoleErrors).toContain('TypeError: cannot read property of null');
  });

  test('passes through networkErrors and errorStack', async () => {
    const client = new LLMClient();
    const result = await client.classifyBug(
      'run-1', 'scenario-1',
      'https://example.com',
      'Network failure',
      [],
      [],
      ['ERR_CONNECTION_REFUSED'],
      'Error: net::ERR_CONNECTION_REFUSED\n  at fetchData (app.js:10)',
    );

    expect(result.networkErrors).toContain('ERR_CONNECTION_REFUSED');
    expect(result.errorStack).toContain('app.js:10');
  });

  test('omits consoleErrors and networkErrors when empty', async () => {
    const client = new LLMClient();
    const result = await client.classifyBug(
      'run-1', 'scenario-1',
      'https://example.com',
      'Some error',
      [],
      [],
      [],
    );

    expect(result.consoleErrors).toBeUndefined();
    expect(result.networkErrors).toBeUndefined();
  });
});
