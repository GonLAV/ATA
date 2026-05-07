import OpenAI from 'openai';
import type { ExplorationMap, PageSnapshot, TestScenario, BugReport, Severity } from '../types';

export type AIProvider = 'openai' | 'anthropic' | 'google' | 'mistral' | 'ollama' | 'custom';

export interface AIProviderConfig {
  provider: AIProvider;
  apiKey?: string;
  model: string;
  baseUrl?: string;
}

export const PROVIDER_DEFAULTS: Record<AIProvider, { model: string; baseUrl?: string; label: string }> = {
  openai:    { model: 'gpt-4o-mini',                   baseUrl: 'https://api.openai.com/v1',          label: 'OpenAI' },
  anthropic: { model: 'claude-3-5-haiku-20241022',      baseUrl: 'https://api.anthropic.com',          label: 'Anthropic' },
  google:    { model: 'gemini-1.5-flash',               baseUrl: 'https://generativelanguage.googleapis.com', label: 'Google' },
  mistral:   { model: 'mistral-small-latest',           baseUrl: 'https://api.mistral.ai/v1',          label: 'Mistral' },
  ollama:    { model: 'llama3.2',                       baseUrl: 'http://localhost:11434/v1',           label: 'Ollama' },
  custom:    { model: 'gpt-4o-mini',                                                                    label: 'Custom' },
};

export const PROVIDER_MODELS: Record<AIProvider, string[]> = {
  openai:    ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'gpt-3.5-turbo'],
  anthropic: ['claude-3-5-sonnet-20241022', 'claude-3-5-haiku-20241022', 'claude-3-haiku-20240307', 'claude-opus-4-7'],
  google:    ['gemini-1.5-pro', 'gemini-1.5-flash', 'gemini-2.0-flash', 'gemini-2.0-flash-lite'],
  mistral:   ['mistral-large-latest', 'mistral-small-latest', 'codestral-latest', 'open-mixtral-8x22b'],
  ollama:    ['llama3.2', 'llama3.1', 'mistral', 'codellama', 'qwen2.5', 'phi4'],
  custom:    ['gpt-4o', 'gpt-4o-mini', 'gpt-3.5-turbo'],
};

/**
 * Universal AI client supporting OpenAI, Anthropic, Google Gemini, Mistral, Ollama, and any
 * OpenAI-compatible endpoint. The interface matches LLMClient so it can be a drop-in replacement.
 */
export class AIProviderClient {
  private config: AIProviderConfig;
  private openaiClient?: OpenAI;
  private isConfigured: boolean;

  constructor(config: AIProviderConfig) {
    this.config = config;
    this.isConfigured = Boolean(config.apiKey && config.apiKey.length > 4);

    // OpenAI-compatible providers use the SDK
    if (this.usesOpenAISDK()) {
      this.openaiClient = new OpenAI({
        apiKey: config.apiKey ?? 'no-key',
        baseURL: this.resolvedBaseUrl(),
      });
    }
  }

  get providerLabel(): string {
    return PROVIDER_DEFAULTS[this.config.provider].label;
  }

  get modelName(): string {
    return this.config.model;
  }

  async generateTestScenarios(
    discovery: PageSnapshot | ExplorationMap,
  ): Promise<Array<Omit<TestScenario, 'id' | 'runId' | 'status'>>> {
    const snapshots = 'snapshots' in discovery ? discovery.snapshots : [discovery];
    const fallback = this.generateHeuristicScenarios(snapshots);

    if (!this.isConfigured) {
      console.warn(`[AIProviderClient] ${this.providerLabel} API key not configured. Using heuristic scenario generation.`);
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
      console.warn(`[AIProviderClient] ${this.providerLabel} scenario generation failed, using heuristics:`, (err as Error).message);
      return fallback;
    }
  }

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
  "title": string,
  "severity": "critical" | "high" | "medium" | "low",
  "description": string,
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

    const userPrompt = JSON.stringify({ url, errorDescription, reproductionSteps, consoleErrors, networkErrors, errorStack }, null, 2);

    const parsed = this.isConfigured
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

  // ─── Provider dispatch ─────────────────────────────────────────────────────

  private async chat(system: string, user: string): Promise<string> {
    switch (this.config.provider) {
      case 'anthropic': return this.chatAnthropic(system, user);
      case 'google':    return this.chatGoogle(system, user);
      default:          return this.chatOpenAICompat(system, user);
    }
  }

  private async chatOpenAICompat(system: string, user: string): Promise<string> {
    const client = this.openaiClient!;
    const response = await client.chat.completions.create({
      model: this.config.model,
      messages: [
        { role: 'system', content: system },
        { role: 'user', content: user },
      ],
      temperature: 0.3,
      response_format: { type: 'json_object' },
    });
    return response.choices[0]?.message?.content ?? '{}';
  }

  private async chatAnthropic(system: string, user: string): Promise<string> {
    const baseUrl = this.resolvedBaseUrl();
    const response = await fetch(`${baseUrl}/v1/messages`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': this.config.apiKey!,
        'anthropic-version': '2023-06-01',
      },
      body: JSON.stringify({
        model: this.config.model,
        max_tokens: 4096,
        system,
        messages: [{ role: 'user', content: user }],
      }),
    });

    if (!response.ok) {
      const err = await response.text();
      throw new Error(`Anthropic API error ${response.status}: ${err}`);
    }

    const data = await response.json() as { content: Array<{ type: string; text: string }> };
    return data.content.find((b) => b.type === 'text')?.text ?? '{}';
  }

  private async chatGoogle(system: string, user: string): Promise<string> {
    const baseUrl = this.resolvedBaseUrl();
    const url = `${baseUrl}/v1beta/models/${this.config.model}:generateContent?key=${this.config.apiKey}`;

    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        system_instruction: { parts: [{ text: system }] },
        contents: [{ role: 'user', parts: [{ text: user }] }],
        generationConfig: {
          temperature: 0.3,
          responseMimeType: 'application/json',
        },
      }),
    });

    if (!response.ok) {
      const err = await response.text();
      throw new Error(`Google Gemini API error ${response.status}: ${err}`);
    }

    const data = await response.json() as { candidates: Array<{ content: { parts: Array<{ text: string }> } }> };
    return data.candidates[0]?.content?.parts[0]?.text ?? '{}';
  }

  // ─── Helpers ───────────────────────────────────────────────────────────────

  private usesOpenAISDK(): boolean {
    return this.config.provider !== 'anthropic' && this.config.provider !== 'google';
  }

  private resolvedBaseUrl(): string {
    if (this.config.baseUrl) return this.config.baseUrl;
    return PROVIDER_DEFAULTS[this.config.provider].baseUrl ?? 'https://api.openai.com/v1';
  }

  private parseJSON<T>(raw: string, fallback: T): T {
    try {
      const parsed = JSON.parse(raw) as Record<string, unknown>;
      if (Array.isArray(parsed)) return parsed as unknown as T;
      const arrayValue = Object.values(parsed).find((v) => Array.isArray(v));
      if (arrayValue !== undefined) return arrayValue as unknown as T;
      return parsed as unknown as T;
    } catch {
      console.warn('[AIProviderClient] Failed to parse response:', raw.slice(0, 200));
      return fallback;
    }
  }

  private generateHeuristicScenarios(snapshots: PageSnapshot[]): Array<Omit<TestScenario, 'id' | 'runId' | 'status'>> {
    const scenarios: Array<Omit<TestScenario, 'id' | 'runId' | 'status'>> = [];
    const visitedTitles = new Set<string>();

    for (const snapshot of snapshots.slice(0, 4)) {
      const startUrl = snapshot.finalUrl ?? snapshot.url;
      const pageLabel = snapshot.title || new URL(startUrl).pathname || 'page';

      if (!visitedTitles.has(`navigation:${startUrl}`) && snapshot.links.length > 0) {
        visitedTitles.add(`navigation:${startUrl}`);
        const link = snapshot.links.find((l) => l.href && !l.href.startsWith('mailto:')) ?? snapshot.links[0];
        scenarios.push({
          title: `Explore primary navigation from ${pageLabel}`,
          description: 'Validate that a prominent discovered link can be used without browser or network failures.',
          priority: 'high',
          steps: [
            { action: 'navigate', value: startUrl, description: `Open ${startUrl}` },
            { action: 'assert_visible', selector: link.selector, description: `Confirm navigation target is visible: ${link.text ?? link.href ?? link.selector}` },
            { action: 'click', selector: link.selector, description: `Open navigation target: ${link.text ?? link.href ?? link.selector}` },
            { action: 'wait', value: '1200', description: 'Wait for navigation to settle' },
            { action: 'screenshot', value: 'navigation_after_click', description: 'Capture navigation result' },
          ],
        });
      }

      for (const form of snapshot.forms.slice(0, 3)) {
        const fillableInputs = form.inputs.filter((i) => i.type !== 'submit' && i.selector);
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
            description: `Submit form${form.id ? ` ${form.id}` : ''}`,
          },
          { action: 'wait' as const, value: '1500', description: 'Wait for response' },
          { action: 'screenshot' as const, value: 'form_submission_result', description: 'Capture result' },
        ];

        scenarios.push({
          title: `Exercise discovered form on ${pageLabel}`,
          description: 'Use inferred realistic data to verify that a discovered form responds correctly.',
          priority: fillableInputs.some((i) => i.type === 'password' || i.type === 'email') ? 'high' : 'medium',
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
            { action: 'screenshot', value: 'button_interaction_result', description: 'Capture result' },
          ],
        });
      }
    }

    return scenarios.slice(0, 12);
  }

  private fakeValueForInput(type: string | undefined, label: string): string {
    const n = `${type ?? ''} ${label}`.toLowerCase();
    if (n.includes('email')) return 'qa.copilot@example.com';
    if (n.includes('password')) return 'QA-Copilot-123!';
    if (n.includes('phone') || n.includes('tel')) return '+15555550199';
    if (n.includes('name')) return 'QA Copilot User';
    if (n.includes('company') || n.includes('org')) return 'Acme QA Labs';
    if (n.includes('search')) return 'dashboard';
    if (n.includes('url')) return 'https://example.com';
    if (n.includes('number')) return '42';
    return 'QA Copilot test value';
  }
}
