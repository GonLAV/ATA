import { getConfig, resetConfigForTests } from '../config/Config';

const originalEnv = { ...process.env };

afterEach(() => {
  process.env = { ...originalEnv };
  resetConfigForTests();
});

describe('Config', () => {
  test('parses explicit false boolean environment values safely', () => {
    process.env.HEADLESS = 'false';
    process.env.ALLOW_PRIVATE_TARGETS = 'false';
    process.env.LOG_HTTP_REQUESTS = 'false';
    resetConfigForTests();

    const config = getConfig();

    expect(config.headless).toBe(false);
    expect(config.allowPrivateTargets).toBe(false);
    expect(config.logHttpRequests).toBe(false);
  });

  test('parses true-like boolean environment values', () => {
    process.env.HEADLESS = '1';
    process.env.ALLOW_PRIVATE_TARGETS = 'yes';
    process.env.LOG_HTTP_REQUESTS = 'on';
    resetConfigForTests();

    const config = getConfig();

    expect(config.headless).toBe(true);
    expect(config.allowPrivateTargets).toBe(true);
    expect(config.logHttpRequests).toBe(true);
  });
});