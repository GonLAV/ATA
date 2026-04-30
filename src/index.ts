import 'dotenv/config';
import { createApp } from './api/server';
import { getConfig } from './config/Config';

const PORT = getConfig().port;
const app = createApp();

app.listen(PORT, () => {
  console.log(`\nQA Copilot server running on http://localhost:${PORT}`);
  console.log(`   Health: GET  http://localhost:${PORT}/health`);
  console.log(`   Runs:   POST http://localhost:${PORT}/api/runs  { "url": "https://example.com" }`);
});
