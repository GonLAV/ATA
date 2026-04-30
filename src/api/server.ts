import express from 'express';
import { randomUUID, timingSafeEqual } from 'crypto';
import routes from './routes';
import { getConfig } from '../config/Config';

const JSON_BODY_LIMIT = '1mb';

/**
 * Creates and configures the Express application.
 * Separated from index.ts so it can be imported in tests.
 */
export function createApp() {
  const app = express();

  app.disable('x-powered-by');
  app.set('trust proxy', 1);

  app.use((req, res, next) => {
    const requestId = randomUUID();
    const startedAt = Date.now();
    res.setHeader('X-Request-Id', requestId);
    res.on('finish', () => {
      if (!getConfig().logHttpRequests) return;
      console.info('[HTTP]', {
        requestId,
        method: req.method,
        path: req.path,
        statusCode: res.statusCode,
        durationMs: Date.now() - startedAt,
      });
    });
    next();
  });

  app.use((_req, res, next) => {
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('X-Frame-Options', 'DENY');
    res.setHeader('Referrer-Policy', 'no-referrer');
    res.setHeader('Permissions-Policy', 'camera=(), microphone=(), geolocation=()');
    res.setHeader('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'; base-uri 'none'");
    next();
  });

  app.use(express.json({ limit: JSON_BODY_LIMIT, strict: true }));

  // Health check
  app.get('/health', (_req, res) => {
    res.json({ status: 'ok', service: 'qa-copilot' });
  });

  // API routes
  app.use('/api', requireApiKey);
  app.use('/api', routes);

  // 404 handler
  app.use((_req, res) => {
    res.status(404).json({ error: 'Not found.' });
  });

  app.use((err: unknown, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
    void _next;

    if (err instanceof SyntaxError) {
      res.status(400).json({ error: 'Malformed JSON request body.' });
      return;
    }

    console.error('[HTTP] Unhandled request error:', err);
    res.status(500).json({ error: 'Internal server error.' });
  });

  return app;
}

function requireApiKey(req: express.Request, res: express.Response, next: express.NextFunction): void {
  const expectedKey = getConfig().apiKey;
  if (!expectedKey) {
    next();
    return;
  }

  const providedKey = extractApiKey(req);
  if (!providedKey || !safeEquals(providedKey, expectedKey)) {
    res.status(401).json({ error: 'Unauthorized.' });
    return;
  }

  next();
}

function extractApiKey(req: express.Request): string | undefined {
  const headerKey = req.header('x-qa-copilot-api-key');
  if (headerKey) return headerKey;

  const authorization = req.header('authorization');
  if (!authorization?.startsWith('Bearer ')) return undefined;
  return authorization.slice('Bearer '.length).trim();
}

function safeEquals(actual: string, expected: string): boolean {
  const actualBuffer = Buffer.from(actual);
  const expectedBuffer = Buffer.from(expected);
  return actualBuffer.length === expectedBuffer.length && timingSafeEqual(actualBuffer, expectedBuffer);
}
