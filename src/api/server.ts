import express from 'express';
import path from 'path';
import routes from './routes';

/**
 * Creates and configures the Express application.
 * Separated from index.ts so it can be imported in tests.
 */
export function createApp() {
  const app = express();

  app.use(express.json());

  // Serve the designer UI
  const publicDir = path.join(__dirname, '../../public');
  app.use(express.static(publicDir));

  // Health check
  app.get('/health', (_req, res) => {
    res.json({ status: 'ok', service: 'qa-copilot' });
  });

  // API routes
  app.use('/api', routes);

  // SPA fallback — serve index.html for any non-API route
  app.get('*', (_req, res) => {
    res.sendFile(path.join(publicDir, 'index.html'));
  });

  return app;
}
