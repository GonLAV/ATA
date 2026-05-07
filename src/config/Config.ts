import { z } from 'zod';
import type { AIProvider, AIProviderConfig } from '../agents/AIProviderClient';
import { PROVIDER_DEFAULTS } from '../agents/AIProviderClient';

const ConfigSchema = z.object({
  port: z.coerce.number().int().positive().default(3000),
  databasePath: z.string().min(1).default('./qa_copilot.db'),
  screenshotsDir: z.string().min(1).default('./screenshots'),
  headless: z.coerce.boolean().default(true),
  browserWidth: z.coerce.number().int().positive().default(1280),
  browserHeight: z.coerce.number().int().positive().default(800),
  stepTimeoutMs: z.coerce.number().int().positive().default(10_000),
  discoveryPageLimit: z.coerce.number().int().positive().max(10).default(4),
  maxScenarios: z.coerce.number().int().positive().max(50).default(12),
  // Legacy OpenAI compat (kept for backward compatibility)
  openAiApiKey: z.string().optional(),
  openAiBaseUrl: z.string().url().optional(),
  openAiModel: z.string().min(1).default('gpt-4o-mini'),
  // Multi-provider
  aiProvider: z.enum(['openai', 'anthropic', 'google', 'mistral', 'ollama', 'custom']).default('openai'),
  anthropicApiKey: z.string().optional(),
  googleApiKey: z.string().optional(),
  mistralApiKey: z.string().optional(),
  aiModel: z.string().optional(),
  aiBaseUrl: z.string().optional(),
});

export type AppConfig = z.infer<typeof ConfigSchema>;

let cachedConfig: AppConfig | undefined;

// Runtime override for AI provider config (set via /api/ai-config endpoint)
let runtimeAIOverride: Partial<AIProviderConfig> | undefined;

export function getConfig(): AppConfig {
  if (cachedConfig) return cachedConfig;

  cachedConfig = ConfigSchema.parse({
    port: process.env.PORT,
    databasePath: process.env.DATABASE_PATH,
    screenshotsDir: process.env.SCREENSHOTS_DIR,
    headless: process.env.HEADLESS,
    browserWidth: process.env.BROWSER_WIDTH,
    browserHeight: process.env.BROWSER_HEIGHT,
    stepTimeoutMs: process.env.STEP_TIMEOUT_MS,
    discoveryPageLimit: process.env.DISCOVERY_PAGE_LIMIT,
    maxScenarios: process.env.MAX_SCENARIOS,
    openAiApiKey: process.env.OPENAI_API_KEY,
    openAiBaseUrl: process.env.OPENAI_BASE_URL,
    openAiModel: process.env.OPENAI_MODEL,
    aiProvider: process.env.AI_PROVIDER,
    anthropicApiKey: process.env.ANTHROPIC_API_KEY,
    googleApiKey: process.env.GOOGLE_API_KEY,
    mistralApiKey: process.env.MISTRAL_API_KEY,
    aiModel: process.env.AI_MODEL,
    aiBaseUrl: process.env.AI_BASE_URL,
  });

  return cachedConfig;
}

export function getActiveAIConfig(): AIProviderConfig {
  const config = getConfig();
  const provider = (runtimeAIOverride?.provider ?? config.aiProvider) as AIProvider;
  const defaults = PROVIDER_DEFAULTS[provider];

  // Resolve API key: runtime override > env-specific key > legacy openAiApiKey
  const resolveApiKey = (): string | undefined => {
    if (runtimeAIOverride?.apiKey) return runtimeAIOverride.apiKey;
    if (provider === 'anthropic') return config.anthropicApiKey;
    if (provider === 'google')    return config.googleApiKey;
    if (provider === 'mistral')   return config.mistralApiKey;
    return config.openAiApiKey;
  };

  return {
    provider,
    apiKey: resolveApiKey(),
    model: runtimeAIOverride?.model ?? config.aiModel ?? config.openAiModel ?? defaults.model,
    baseUrl: runtimeAIOverride?.baseUrl ?? config.aiBaseUrl ?? config.openAiBaseUrl ?? defaults.baseUrl,
  };
}

export function setRuntimeAIConfig(override: Partial<AIProviderConfig>): void {
  runtimeAIOverride = { ...runtimeAIOverride, ...override };
}

export function resetConfigForTests(): void {
  cachedConfig = undefined;
  runtimeAIOverride = undefined;
}
