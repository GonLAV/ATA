import { z } from 'zod';

const booleanEnv = (defaultValue: boolean) => z.preprocess((value) => {
  if (value === undefined || value === '') return undefined;
  if (typeof value === 'boolean') return value;
  if (typeof value !== 'string') return value;

  const normalized = value.trim().toLowerCase();
  if (['true', '1', 'yes', 'on'].includes(normalized)) return true;
  if (['false', '0', 'no', 'off'].includes(normalized)) return false;
  return value;
}, z.boolean().default(defaultValue));

const ConfigSchema = z.object({
  port: z.coerce.number().int().positive().default(3000),
  databasePath: z.string().min(1).default('./qa_copilot.db'),
  screenshotsDir: z.string().min(1).default('./screenshots'),
  headless: booleanEnv(true),
  browserWidth: z.coerce.number().int().positive().default(1280),
  browserHeight: z.coerce.number().int().positive().default(800),
  stepTimeoutMs: z.coerce.number().int().positive().default(10_000),
  discoveryPageLimit: z.coerce.number().int().positive().max(10).default(4),
  maxScenarios: z.coerce.number().int().positive().max(50).default(12),
  allowPrivateTargets: booleanEnv(false),
  releaseGateMinRiskScore: z.coerce.number().int().min(0).max(100).default(75),
  releaseGateMaxHighSeverityBugs: z.coerce.number().int().min(0).default(0),
  releaseGateMaxHighSeverityRisks: z.coerce.number().int().min(0).default(2),
  releaseGateMaxFailedScenarioRate: z.coerce.number().min(0).max(1).default(0.25),
  apiKey: z.string().min(16).optional(),
  apiRateLimitWindowMs: z.coerce.number().int().positive().default(60_000),
  apiRateLimitMax: z.coerce.number().int().min(0).default(120),
  trustProxyHops: z.coerce.number().int().min(0).default(0),
  logHttpRequests: booleanEnv(process.env.NODE_ENV !== 'test'),
  openAiApiKey: z.string().optional(),
  openAiBaseUrl: z.string().url().optional(),
  openAiModel: z.string().min(1).default('gpt-4o-mini'),
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
    allowPrivateTargets: process.env.ALLOW_PRIVATE_TARGETS,
    releaseGateMinRiskScore: process.env.RELEASE_GATE_MIN_RISK_SCORE,
    releaseGateMaxHighSeverityBugs: process.env.RELEASE_GATE_MAX_HIGH_SEVERITY_BUGS,
    releaseGateMaxHighSeverityRisks: process.env.RELEASE_GATE_MAX_HIGH_SEVERITY_RISKS,
    releaseGateMaxFailedScenarioRate: process.env.RELEASE_GATE_MAX_FAILED_SCENARIO_RATE,
    apiKey: process.env.QA_COPILOT_API_KEY,
    apiRateLimitWindowMs: process.env.API_RATE_LIMIT_WINDOW_MS,
    apiRateLimitMax: process.env.API_RATE_LIMIT_MAX,
    trustProxyHops: process.env.TRUST_PROXY_HOPS,
    logHttpRequests: process.env.LOG_HTTP_REQUESTS,
    openAiApiKey: process.env.OPENAI_API_KEY,
    openAiBaseUrl: process.env.OPENAI_BASE_URL,
    openAiModel: process.env.OPENAI_MODEL,
  });

  return cachedConfig;
}

export function resetConfigForTests(): void {
  cachedConfig = undefined;
}