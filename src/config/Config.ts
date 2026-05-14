import { z } from 'zod';

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
  openAiApiKey: z.string().optional(),
  openAiBaseUrl: z.string().url().optional(),
  openAiModel: z.string().min(1).default('gpt-4o'),
});

export type AppConfig = z.infer<typeof ConfigSchema>;

let cachedConfig: AppConfig | undefined;

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
  });

  return cachedConfig;
}

export function resetConfigForTests(): void {
  cachedConfig = undefined;
}