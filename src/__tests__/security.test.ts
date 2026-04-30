import { lookup } from 'node:dns/promises';
import type { BrowserContext, Request, Route } from 'playwright';
import { installTargetNetworkGuard, isPrivateHostname, validateTargetUrl } from '../api/security';
import { resetConfigForTests } from '../config/Config';

type LookupAllResult = Array<{ address: string; family: 4 | 6 }>;

jest.mock('node:dns/promises', () => ({
  lookup: jest.fn(),
}));

const mockedLookup = lookup as jest.MockedFunction<typeof lookup>;
const originalEnv = { ...process.env };

afterEach(() => {
  process.env = { ...originalEnv };
  resetConfigForTests();
  mockedLookup.mockReset();
});

describe('API target security', () => {
  test.each([
    'localhost',
    'app.localhost',
    '127.0.0.1',
    '10.0.0.1',
    '172.16.0.1',
    '192.168.1.10',
    '169.254.1.1',
    '::1',
    'fd00::1',
  ])('classifies %s as private/local', (hostname) => {
    expect(isPrivateHostname(hostname)).toBe(true);
  });

  test('blocks public hostname that resolves to a private address', async () => {
    mockedLookup.mockResolvedValue([{ address: '127.0.0.1', family: 4 }] as LookupAllResult as never);
    const result = await validateTargetUrl('https://example.test/dashboard');

    expect(result.allowed).toBe(false);
    expect(result.error).toContain('private or local');
  });

  test('allows public hostname that resolves to public addresses', async () => {
    mockedLookup.mockResolvedValue([{ address: '93.184.216.34', family: 4 }] as LookupAllResult as never);
    const result = await validateTargetUrl('https://example.com/dashboard');

    expect(result.allowed).toBe(true);
    expect(result.normalizedUrl).toBe('https://example.com/dashboard');
  });

  test('installs a browser network guard that blocks private resolved requests', async () => {
    let routeHandler: Parameters<BrowserContext['route']>[1] | undefined;
    const context = {
      route: jest.fn(async (_pattern: string, handler: Parameters<BrowserContext['route']>[1]) => {
        routeHandler = handler;
      }),
    } as unknown as BrowserContext;
    const abort = jest.fn();
    const continueRequest = jest.fn();
    mockedLookup.mockResolvedValue([{ address: '127.0.0.1', family: 4 }] as LookupAllResult as never);

    await installTargetNetworkGuard(context);
    expect(routeHandler).toBeDefined();

    await routeHandler!({
      abort,
      continue: continueRequest,
    } as unknown as Route, {
      url: () => 'https://asset.example.test/script.js',
    } as unknown as Request);

    expect(abort).toHaveBeenCalledWith('blockedbyclient');
    expect(continueRequest).not.toHaveBeenCalled();
  });
});