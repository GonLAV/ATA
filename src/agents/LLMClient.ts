import OpenAI from 'openai';
import type { ExplorationMap, PageSnapshot, TestScenario, BugReport, Severity } from '../types';
import { getConfig } from '../config/Config';

const VALID_ACTIONS = new Set([
  'navigate', 'click', 'fill', 'submit', 'wait',
  'screenshot', 'assert_visible', 'assert_text', 'assert_url',
]);
const VALID_PRIORITIES = new Set(['high', 'medium', 'low']);
const VALID_SEVERITIES = new Set(['critical', 'high', 'medium', 'low']);

// JSON schemas for structured outputs — strict mode eliminates hallucinated fields
// and guarantees enum values match the application's type system.
const SCENARIO_SCHEMA = {
  type: 'object',
  properties: {
    scenarios: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          title: { type: 'string' },
          description: { type: 'string' },
          priority: { type: 'string', enum: ['high', 'medium', 'low'] },
          steps: {
            type: 'array',
            items: {
              type: 'object',
              properties: {
                action: {
                  type: 'string',
                  enum: ['navigate', 'click', 'fill', 'submit', 'wait', 'screenshot',
                    'assert_visible', 'assert_text', 'assert_url'],
                },
                selector: { anyOf: [{ type: 'string' }, { type: 'null' }] },
                value: { anyOf: [{ type: 'string' }, { type: 'null' }] },
                description: { type: 'string' },
              },
              required: ['action', 'selector', 'value', 'description'],
              additionalProperties: false,
            },
          },
        },
        required: ['title', 'description', 'priority', 'steps'],
        additionalProperties: false,
      },
    },
  },
  required: ['scenarios'],
  additionalProperties: false,
};

const BUG_SCHEMA = {
  type: 'object',
  properties: {
    title: { type: 'string' },
    severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
    description: { type: 'string' },
    reproductionSteps: { type: 'array', items: { type: 'string' } },
    expectedBehavior: { type: 'string' },
    actualBehavior: { type: 'string' },
    url: { type: 'string' },
  },
  required: ['title', 'severity', 'description', 'reproductionSteps', 'expectedBehavior', 'actualBehavior', 'url'],
  additionalProperties: false,
};

/**
 * Thin wrapper around the OpenAI chat-completions API.
 * Supports any OpenAI-compatible endpoint (Azure, Ollama, etc.) via env vars.
 */
export class LLMClient {
  private client: OpenAI;
  private model: string;
  private hasConfiguredModel: boolean;

  constructor() {
    const config = getConfig();
    const apiKey = config.openAiApiKey;
    this.client = new OpenAI({
      apiKey: apiKey ?? 'no-key',
      baseURL: config.openAiBaseUrl,
    });
    this.model = config.openAiModel;
    this.hasConfiguredModel = Boolean(apiKey && apiKey !== 'sk-...' && apiKey !== 'test-key');
  }

  /**
   * Ask the LLM to generate a list of test scenarios for the given page snapshot.
   * Returns an array of partial TestScenario objects (without id/runId/status).
   */
  async generateTestScenarios(
    discovery: PageSnapshot | ExplorationMap,
  ): Promise<Array<Omit<TestScenario, 'id' | 'runId' | 'status'>>> {
    const snapshots = 'snapshots' in discovery ? discovery.snapshots : [discovery];
    const fallback = this.generateHeuristicScenarios(snapshots);

    if (!this.hasConfiguredModel) {
      console.warn('[LLMClient] OPENAI_API_KEY is not configured. Using dynamic heuristic scenario generation.');
      return fallback;
    }

    const systemPrompt = `You are a senior QA automation engineer creating end-to-end test scenarios.

Given JSON exploration data for a web application, generate realistic test scenarios a QA agent will execute.

Rules:
- Cover navigation, form submission, UI interactions, and error states.
- Infer realistic test data from field names and placeholders — never hardcode generic values.
- Selectors MUST be valid CSS selectors copied verbatim from the snapshot data.
- Each scenario must have between 2 and 8 steps.
- Use null for selector/value when the action does not require it (e.g. navigate, wait, screenshot).
- Prioritise "high" for auth flows, payments, and core conversion paths.

Action type guide:
  navigate   — load a URL (value = URL, selector = null)
  click      — click an element (selector required, value = null)
  fill       — type into an input (selector + value required)
  submit     — submit a form (selector = form or submit button)
  wait       — pause in ms (value = ms as string, selector = null)
  screenshot — capture screenshot (value = filename slug, selector = null)
  assert_visible — assert element exists in DOM (selector required)
  assert_text    — assert element contains text (selector + value required)
  assert_url     — assert page URL contains string (value required, selector = null)

Example scenario:
{
  "title": "Submit login form with valid credentials",
  "description": "Verifies the login form accepts credentials and redirects to the dashboard.",
  "priority": "high",
  "steps": [
    { "action": "navigate", "selector": null, "value": "https://example.com/login", "description": "Open login page" },
    { "action": "fill", "selector": "#email", "value": "qa@example.com", "description": "Enter email" },
    { "action": "fill", "selector": "#password", "value": "QA-Test-123!", "description": "Enter password" },
    { "action": "click", "selector": "button[type=submit]", "value": null, "description": "Click login" },
    { "action": "assert_url", "selector": null, "value": "/dashboard", "description": "Confirm redirect to dashboard" }
  ]
}`;

    const userPrompt = `Exploration data:\n${JSON.stringify(discovery, null, 2)}`;

    try {
      const raw = await this.chat(systemPrompt, userPrompt, {
        schema: { name: 'test_scenarios', schema: SCENARIO_SCHEMA },
        temperature: 0.2,
      });
      const parsed = this.parseJSON<Array<Omit<TestScenario, 'id' | 'runId' | 'status'>>>(raw, fallback);
      const validated = this.validateScenarios(parsed);
      return validated.length > 0 ? validated : fallback;
    } catch (err) {
      console.warn('[LLMClient] LLM scenario generation failed. Falling back to dynamic heuristics:', (err as Error).message);
      return fallback;
    }
  }

  /**
   * Ask the LLM to classify and enrich a raw error observation into a structured BugReport.
   */
  async classifyBug(
    runId: string,
    scenarioId: string,
    url: string,
    errorDescription: string,
    reproductionSteps: string[],
    consoleErrors: string[],
    networkErrors: string[],
    errorStack?: string,
  ): Promise<Omit<BugReport, 'id' | 'runId' | 'scenarioId' | 'detectedAt'>> {
    const systemPrompt = `You are a senior QA engineer writing a structured bug report.
Given information about a test failure, produce a precise, actionable bug report.

Severity guide — pick the LOWEST severity that accurately describes the impact:
  critical — app crash, data loss, security bypass, complete feature outage
  high     — core feature broken for all users, checkout/auth/data-save blocked
  medium   — partial feature failure, degraded UX, non-blocking error
  low      — cosmetic defect, minor wording, non-impactful visual glitch

Rules:
- Use the EXACT url from the input, do not invent or alter it.
- Reproduction steps must be concrete, numbered actions a developer can follow.
- Expected and actual behavior must differ meaningfully.
- Title must be a concise, actionable sentence (max 80 chars).`;

    const userPrompt = JSON.stringify({
      url,
      errorDescription,
      reproductionSteps,
      consoleErrors,
      networkErrors,
      errorStack,
    }, null, 2);

    const parsed = this.hasConfiguredModel
      ? this.parseJSON<Partial<BugReport>>(
          await this.chat(systemPrompt, userPrompt, {
            schema: { name: 'bug_report', schema: BUG_SCHEMA },
            temperature: 0,
          }),
          {},
        )
      : {};

    return {
      title: parsed.title ?? 'Unknown issue',
      severity: (VALID_SEVERITIES.has(parsed.severity as string) ? parsed.severity : 'medium') as Severity,
      description: parsed.description ?? errorDescription,
      reproductionSteps: parsed.reproductionSteps ?? reproductionSteps,
      expectedBehavior: parsed.expectedBehavior ?? 'The action should complete successfully.',
      actualBehavior: parsed.actualBehavior ?? errorDescription,
      url,
      consoleErrors: consoleErrors.length > 0 ? consoleErrors : undefined,
      networkErrors: networkErrors.length > 0 ? networkErrors : undefined,
      errorStack,
    };
  }

  // ─── Private helpers ───────────────────────────────────────────────────────

  private async chat(
    system: string,
    user: string,
    options: { schema?: { name: string; schema: Record<string, unknown> }; temperature?: number } = {},
  ): Promise<string> {
    const { schema, temperature = 0.2 } = options;

    const buildRequest = (useSchema: boolean) => ({
      model: this.model,
      messages: [
        { role: 'system' as const, content: system },
        { role: 'user' as const, content: user },
      ],
      temperature,
      response_format: (useSchema && schema)
        ? { type: 'json_schema' as const, json_schema: { name: schema.name, strict: true, schema: schema.schema } }
        : { type: 'json_object' as const },
    });

    try {
      const response = await this.client.chat.completions.create(buildRequest(true));
      return response.choices[0]?.message?.content ?? '{}';
    } catch (err) {
      // Structured outputs are not supported by all endpoints (e.g. Ollama, older Azure).
      // Fall back to json_object mode so custom deployments keep working.
      const msg = (err as Error).message ?? '';
      if (schema && (msg.includes('json_schema') || msg.includes('response_format') || msg.includes('not supported'))) {
        console.warn('[LLMClient] Structured outputs not supported by this endpoint — falling back to json_object mode.');
        const response = await this.client.chat.completions.create(buildRequest(false));
        return response.choices[0]?.message?.content ?? '{}';
      }
      throw err;
    }
  }

  private parseJSON<T>(raw: string, fallback: T): T {
    try {
      const parsed = JSON.parse(raw) as Record<string, unknown>;
      if (Array.isArray(parsed)) return parsed as unknown as T;
      const arrayValue = Object.values(parsed).find((v) => Array.isArray(v));
      if (arrayValue !== undefined) return arrayValue as unknown as T;
      return parsed as unknown as T;
    } catch {
      console.warn('[LLMClient] Failed to parse LLM response:', raw.slice(0, 200));
      return fallback;
    }
  }

  private validateScenarios(
    scenarios: Array<Omit<TestScenario, 'id' | 'runId' | 'status'>>,
  ): Array<Omit<TestScenario, 'id' | 'runId' | 'status'>> {
    return scenarios
      .filter((s) => s.title && Array.isArray(s.steps))
      .map((s) => ({
        ...s,
        priority: VALID_PRIORITIES.has(s.priority) ? s.priority : 'medium',
        steps: s.steps
          .filter((step) => VALID_ACTIONS.has(step.action))
          .slice(0, 8),
      }))
      .filter((s) => s.steps.length >= 2)
      .slice(0, 12);
  }

  private generateHeuristicScenarios(
    snapshots: PageSnapshot[],
  ): Array<Omit<TestScenario, 'id' | 'runId' | 'status'>> {
    const scenarios: Array<Omit<TestScenario, 'id' | 'runId' | 'status'>> = [];
    const visitedTitles = new Set<string>();

    for (const snapshot of snapshots.slice(0, 4)) {
      const startUrl = snapshot.finalUrl ?? snapshot.url;
      const pageLabel = snapshot.title || new URL(startUrl).pathname || 'page';

      if (!visitedTitles.has(`navigation:${startUrl}`) && snapshot.links.length > 0) {
        visitedTitles.add(`navigation:${startUrl}`);
        const link = snapshot.links.find((candidate) => candidate.href && !candidate.href.startsWith('mailto:')) ?? snapshot.links[0];
        scenarios.push({
          title: `Explore primary navigation from ${pageLabel}`,
          description: 'Validate that a prominent discovered link can be used without browser or network failures.',
          priority: 'high',
          steps: [
            { action: 'navigate', value: startUrl, description: `Open ${startUrl}` },
            { action: 'assert_visible', selector: link.selector, description: `Confirm navigation target is visible: ${link.text ?? link.href ?? link.selector}` },
            { action: 'click', selector: link.selector, description: `Open discovered navigation target: ${link.text ?? link.href ?? link.selector}` },
            { action: 'wait', value: '1200', description: 'Wait for navigation or UI transition to settle' },
            { action: 'screenshot', value: 'navigation_after_click', description: 'Capture navigation result' },
          ],
        });
      }

      for (const form of snapshot.forms.slice(0, 3)) {
        const fillableInputs = form.inputs.filter((input) => input.type !== 'submit' && input.selector);
        if (fillableInputs.length === 0) continue;

        const steps = [
          { action: 'navigate' as const, value: startUrl, description: `Open ${startUrl}` },
          ...fillableInputs.slice(0, 5).map((input) => ({
            action: 'fill' as const,
            selector: input.selector,
            value: this.fakeValueForInput(input.type, `${input.name ?? ''} ${input.placeholder ?? ''}`),
            description: `Fill ${input.placeholder ?? input.name ?? input.type ?? input.selector}`,
          })),
          {
            action: 'submit' as const,
            selector: form.submitButton?.selector ?? fillableInputs[fillableInputs.length - 1].selector,
            description: `Submit discovered form${form.id ? ` ${form.id}` : ''}`,
          },
          { action: 'wait' as const, value: '1500', description: 'Wait for validation, navigation, or API response' },
          { action: 'screenshot' as const, value: 'form_submission_result', description: 'Capture form submission result' },
        ];

        scenarios.push({
          title: `Exercise discovered form on ${pageLabel}`,
          description: 'Use inferred realistic data to verify that a discovered form responds correctly.',
          priority: fillableInputs.some((input) => input.type === 'password' || input.type === 'email') ? 'high' : 'medium',
          steps,
        });
      }

      for (const button of snapshot.buttons.slice(0, 4)) {
        scenarios.push({
          title: `Verify button interaction: ${button.text ?? button.ariaLabel ?? button.selector}`,
          description: 'Click a discovered UI control and capture whether it produces visible browser evidence.',
          priority: /sign|login|checkout|save|submit|continue/i.test(`${button.text ?? ''} ${button.ariaLabel ?? ''}`) ? 'high' : 'medium',
          steps: [
            { action: 'navigate', value: startUrl, description: `Open ${startUrl}` },
            { action: 'assert_visible', selector: button.selector, description: `Confirm button is visible: ${button.text ?? button.selector}` },
            { action: 'click', selector: button.selector, description: `Click ${button.text ?? button.ariaLabel ?? button.selector}` },
            { action: 'wait', value: '1000', description: 'Wait for UI response' },
            { action: 'screenshot', value: 'button_interaction_result', description: 'Capture button interaction result' },
          ],
        });
      }
    }

    return scenarios.slice(0, 12);
  }

  private fakeValueForInput(type: string | undefined, label: string): string {
    const normalized = `${type ?? ''} ${label}`.toLowerCase();
    if (normalized.includes('email')) return 'qa.copilot@example.com';
    if (normalized.includes('password')) return 'QA-Copilot-123!';
    if (normalized.includes('phone') || normalized.includes('tel')) return '+15555550199';
    if (normalized.includes('name')) return 'QA Copilot User';
    if (normalized.includes('company') || normalized.includes('org')) return 'Acme QA Labs';
    if (normalized.includes('search')) return 'dashboard';
    if (normalized.includes('url')) return 'https://example.com';
    if (normalized.includes('number')) return '42';
    return 'QA Copilot test value';
  }
}
