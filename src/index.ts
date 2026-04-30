import 'dotenv/config';
import { createApp } from './api/server';

const PORT = parseInt(process.env.PORT ?? '3000', 10);
const app = createApp();

app.listen(PORT, () => {
  console.log(`\n🚀 QA Copilot server running on http://localhost:${PORT}`);
  console.log(`   Health: GET  http://localhost:${PORT}/health`);
  console.log(`   Runs:   POST http://localhost:${PORT}/api/runs  { "url": "https://example.com" }`);
});
