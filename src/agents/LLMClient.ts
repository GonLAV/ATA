import OpenAI from 'openai';
import type { ExplorationMap, PageSnapshot, TestScenario, BugReport, Severity } from '../types';
import { getConfig } from '../config/Config';

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

    const systemPrompt = `You are a senior QA automation engineer.
Given JSON exploration data for a web application (URLs, titles, forms, buttons, links, inputs),
generate realistic end-to-end test scenarios for a QA agent to execute.

Rules:
- Do NOT hardcode any specific values — infer them from the page structure.
- Cover: navigation, form submission, UI interactions, error states.
- For input fields infer realistic placeholder data (use plausible but fake test data).
- Selectors must be valid CSS selectors derived from the snapshot.
- Each scenario must have 2–8 steps using these action types:
    navigate | click | fill | submit | wait | screenshot | assert_visible | assert_text | assert_url
- Return ONLY a valid JSON object with a "scenarios" array — no markdown fences, no extra text.

JSON schema for each scenario:
{
  "title": string,
  "description": string,
  "priority": "high" | "medium" | "low",
  "steps": [
    {
      "action": ActionType,
      "selector": string | undefined,
      "value": string | undefined,
      "description": string
    }
  ]
}`;

    const userPrompt = `Exploration data:\n${JSON.stringify(discovery, null, 2)}`;

    try {
      const raw = await this.chat(systemPrompt, userPrompt);
      const parsed = this.parseJSON<Array<Omit<TestScenario, 'id' | 'runId' | 'status'>>>(raw, fallback);
      return parsed.length > 0 ? parsed : fallback;
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
Given information about a test failure, return a JSON bug report.

JSON schema:
{
  "title": string,           // concise, actionable title
  "severity": "critical" | "high" | "medium" | "low",
  "description": string,     // full description of the bug
  "reproductionSteps": string[],
  "expectedBehavior": string,
  "actualBehavior": string,
  "url": string
}

Severity guide:
- critical: app crash, data loss, auth bypass
- high: core feature broken, major UX blocker
- medium: partial feature failure, unexpected behavior
- low: cosmetic, minor UX issue

Return ONLY valid JSON — no markdown fences, no extra text.`;

    const userPrompt = JSON.stringify({
      url,
      errorDescription,
      reproductionSteps,
      consoleErrors,
      networkErrors,
      errorStack,
    }, null, 2);

    const parsed = this.hasConfiguredModel
      ? this.parseJSON<Partial<BugReport>>(await this.chat(systemPrompt, userPrompt), {})
      : {};

    return {
      title: parsed.title ?? 'Unknown issue',
      severity: (parsed.severity ?? 'medium') as Severity,
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

  private async chat(system: string, user: string): Promise<string> {
    const response = await this.client.chat.completions.create({
      model: this.model,
      messages: [
        { role: 'system', content: system },
        { role: 'user', content: user },
      ],
      temperature: 0.3,
      response_format: { type: 'json_object' },
    });
    return response.choices[0]?.message?.content ?? '{}';
  }

  private parseJSON<T>(raw: string, fallback: T): T {
    try {
      // The model sometimes returns a top-level object with a key like "scenarios"
      const parsed = JSON.parse(raw) as Record<string, unknown>;
      // If it is already an array, return it
      if (Array.isArray(parsed)) return parsed as unknown as T;
      // Otherwise look for the first array value inside the object
      const arrayValue = Object.values(parsed).find((v) => Array.isArray(v));
      if (arrayValue !== undefined) return arrayValue as unknown as T;
      return parsed as unknown as T;
    } catch {
      console.warn('[LLMClient] Failed to parse LLM response:', raw.slice(0, 200));
      return fallback;
    }
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
