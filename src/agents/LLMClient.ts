import OpenAI from 'openai';
import type { TestStep, PageSnapshot, TestScenario, BugReport, Severity } from '../types';

/**
 * Thin wrapper around the OpenAI chat-completions API.
 * Supports any OpenAI-compatible endpoint (Azure, Ollama, etc.) via env vars.
 */
export class LLMClient {
  private client: OpenAI;
  private model: string;

  constructor() {
    this.client = new OpenAI({
      apiKey: process.env.OPENAI_API_KEY ?? 'no-key',
      baseURL: process.env.OPENAI_BASE_URL,
    });
    this.model = process.env.OPENAI_MODEL ?? 'gpt-4o-mini';
  }

  /**
   * Ask the LLM to generate a list of test scenarios for the given page snapshot.
   * Returns an array of partial TestScenario objects (without id/runId/status).
   */
  async generateTestScenarios(
    snapshot: PageSnapshot,
  ): Promise<Array<Omit<TestScenario, 'id' | 'runId' | 'status'>>> {
    const systemPrompt = `You are a senior QA automation engineer.
Given a JSON snapshot of a web page (URL, title, forms, buttons, links, inputs),
generate realistic end-to-end test scenarios for a QA agent to execute.

Rules:
- Do NOT hardcode any specific values — infer them from the page structure.
- Cover: navigation, form submission, UI interactions, error states.
- For input fields infer realistic placeholder data (use plausible but fake test data).
- Selectors must be valid CSS selectors derived from the snapshot.
- Each scenario must have 2–8 steps using these action types:
    navigate | click | fill | submit | wait | screenshot | assert_visible | assert_text | assert_url
- Return ONLY a valid JSON array — no markdown fences, no extra text.

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

    const userPrompt = `Page snapshot:\n${JSON.stringify(snapshot, null, 2)}`;

    const raw = await this.chat(systemPrompt, userPrompt);
    return this.parseJSON<Array<Omit<TestScenario, 'id' | 'runId' | 'status'>>>(raw, []);
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

    const raw = await this.chat(systemPrompt, userPrompt);
    const parsed = this.parseJSON<Partial<BugReport>>(raw, {});

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
}
