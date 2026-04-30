import express from 'express';
import routes from './routes';

/**
 * Creates and configures the Express application.
 * Separated from index.ts so it can be imported in tests.
 */
export function createApp() {
  const app = express();

  app.use(express.json());

  // Health check
  app.get('/health', (_req, res) => {
    res.json({ status: 'ok', service: 'qa-copilot' });
  });

  // API routes
  app.use('/api', routes);

  // 404 handler
  app.use((_req, res) => {
    res.status(404).json({ error: 'Not found.' });
  });

  return app;
}
