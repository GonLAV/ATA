# QA Copilot — Autonomous AI Testing Agent

An autonomous AI QA testing agent that explores web applications like a human QA engineer, generates test scenarios dynamically, executes them in a real browser, and produces structured bug reports — all without any hardcoded test cases.

---

## Architecture

```
qa-copilot/
├── src/
│   ├── agents/
│   │   ├── QAAgent.ts        # Main orchestrator (explore → generate → execute → report)
│   │   └── LLMClient.ts      # OpenAI-compatible LLM wrapper
│   ├── api/
│   │   ├── routes.ts         # Express REST API endpoints
│   │   └── server.ts         # Express app factory
│   ├── browser/
│   │   ├── BrowserManager.ts # Playwright browser lifecycle
│   │   ├── PageExplorer.ts   # Page snapshot extraction
│   │   └── ScreenshotManager.ts # Screenshot capture
│   ├── database/
│   │   ├── Database.ts       # SQLite connection + schema
│   │   └── Repository.ts     # CRUD for runs, scenarios, bugs
│   ├── reporters/
│   │   └── BugReporter.ts    # Markdown report + dashboard builder
│   ├── types/
│   │   └── index.ts          # Shared TypeScript types
│   └── index.ts              # Entry point
├── .env.example
├── package.json
└── tsconfig.json
```

### Flow

```
POST /api/runs { url }
       │
       ▼
  1. Explore page → PageSnapshot
       │   (buttons, forms, links, console/network errors, screenshot)
       ▼
  2. LLM generates TestScenarios (dynamic, no hardcoding)
       │
       ▼
  3. Execute each scenario in isolated Playwright context
       │   (click, fill, navigate, assert_visible, …)
       ▼
  4. Detect failures (exceptions, console errors, HTTP 4xx/5xx)
       │
       ▼
  5. LLM classifies each failure → BugReport
       │   (title, severity, reproduction steps, expected vs actual)
       ▼
  6. Persist to SQLite; return Dashboard
```

---

## Quick Start

### Prerequisites

- Node.js ≥ 18
- An OpenAI API key (or any OpenAI-compatible endpoint)

### Install

```bash
npm install
npx playwright install chromium
```

### Configure

```bash
cp .env.example .env
# Edit .env — set your OPENAI_API_KEY at minimum
```

### Start the server

```bash
npm run build
npm start
# Server starts on http://localhost:3000
```

### Trigger a QA run

```bash
curl -X POST http://localhost:3000/api/runs \
  -H "Content-Type: application/json" \
  -d '{"url": "https://your-app.example.com"}'
# → {"runId":"<uuid>","message":"QA run started. Poll GET /api/runs/<uuid> for status."}
```

### Poll for status

```bash
curl http://localhost:3000/api/runs/<runId>
```

### Get the full Markdown report

```bash
curl http://localhost:3000/api/runs/<runId>/report
```

### Get the structured JSON dashboard

```bash
curl http://localhost:3000/api/runs/<runId>/dashboard
```

---

## REST API Reference

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/runs` | Start a new QA run |
| `GET` | `/api/runs` | List all runs |
| `GET` | `/api/runs/:id` | Get run status |
| `GET` | `/api/runs/:id/dashboard` | Full JSON dashboard |
| `GET` | `/api/runs/:id/report` | Markdown report |
| `GET` | `/api/runs/:id/bugs` | All bugs for a run |
| `GET` | `/api/runs/:id/scenarios` | All test scenarios for a run |
| `GET` | `/health` | Health check |

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | _(required)_ | OpenAI API key |
| `OPENAI_BASE_URL` | OpenAI default | Override for Azure / Ollama / LM Studio |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model for test generation |
| `PORT` | `3000` | HTTP server port |
| `DATABASE_PATH` | `./qa_copilot.db` | SQLite file path |
| `SCREENSHOTS_DIR` | `./screenshots` | Directory for screenshots |
| `HEADLESS` | `true` | Set to `false` for headed browser |
| `BROWSER_WIDTH` | `1280` | Viewport width |
| `BROWSER_HEIGHT` | `800` | Viewport height |

---

## Bug Report Format

Each detected bug includes:

- **Title** — concise, actionable
- **Severity** — `critical` / `high` / `medium` / `low`
- **Description** — full narrative
- **Reproduction Steps** — numbered list
- **Expected vs Actual Behaviour**
- **Screenshot** — full-page PNG
- **Console Errors** — captured from the browser
- **Network Errors** — HTTP 4xx/5xx and failed requests

---

## Development

```bash
# Type-check
npx tsc --noEmit

# Run tests
npm test

# Lint
npm run lint

# Dev mode (ts-node, no build step)
npm run dev
```

---

## CI/CD Integration

The REST API is designed to be called from CI pipelines:

```yaml
# Example GitHub Actions step
- name: Run QA Copilot
  run: |
    RUN_ID=$(curl -s -X POST $QA_COPILOT_URL/api/runs \
      -H "Content-Type: application/json" \
      -d "{\"url\": \"$DEPLOY_URL\"}" | jq -r .runId)
    
    # Wait for completion
    while true; do
      STATUS=$(curl -s $QA_COPILOT_URL/api/runs/$RUN_ID | jq -r .status)
      [ "$STATUS" = "completed" ] || [ "$STATUS" = "failed" ] && break
      sleep 10
    done
    
    # Download report
    curl -s $QA_COPILOT_URL/api/runs/$RUN_ID/report > qa-report.md
```

