# QA Copilot — Autonomous AI Testing Agent

An autonomous AI QA testing agent that explores web applications like a human QA engineer, generates test scenarios dynamically, executes them in a real browser, and produces structured bug reports — all without any hardcoded test cases.

---

## Architecture

```
qa-copilot/
├── src/
│   ├── agents/
│   │   ├── QAAgent.ts        # Main orchestrator (explore → generate → execute → report)
│   │   ├── LLMClient.ts      # OpenAI-compatible LLM wrapper + heuristic fallback
│   │   └── ProductRiskRadar.ts # Product risk signal engine
│   ├── api/
│   │   ├── routes.ts         # Express REST API endpoints
│   │   └── server.ts         # Express app factory
│   ├── browser/
│   │   ├── BrowserManager.ts # Playwright browser lifecycle
│   │   ├── PageExplorer.ts   # Page snapshot extraction
│   │   └── ScreenshotManager.ts # Screenshot capture
│   ├── contracts/
│   │   └── RegressionContractBuilder.ts # Converts findings into Playwright specs
│   ├── database/
│   │   ├── Database.ts       # SQLite adapter + schema
│   │   └── Repository.ts     # CRUD for runs, scenarios, bugs, risks
│   ├── gates/
│   │   └── ReleaseGateEvaluator.ts # CI/CD ship/warn/block release decisions
│   ├── config/
│   │   └── Config.ts         # Typed environment configuration
│   ├── observability/
│   │   └── Observability.ts  # In-process events and counters
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
  1. Explore app → ExplorationMap
       │   (same-origin pages, controls, forms, links, headings, screenshots)
       ▼
  2. LLM or dynamic heuristic engine generates TestScenarios
       │
       ▼
  3. Execute each scenario in isolated Playwright context
       │   (click, fill, navigate, assert_visible, screenshots, action observations)
       ▼
  4. Detect failures and silent product risks
       │   (exceptions, console errors, HTTP 4xx/5xx, dead interactions, accessibility gaps)
       │
       ▼
  5. LLM classifies each failure → BugReport
       │   (title, severity, reproduction steps, expected vs actual)
       ▼
  6. Persist to SQLite; return Dashboard + Product Risk Radar score
```

### Production Design Notes

- **Dynamic behavior by default:** scenarios are inferred from discovered page structure. If `OPENAI_API_KEY` is unavailable, QA Copilot uses a deterministic generator based on live forms, buttons, and links instead of canned tests.
- **Runtime-resilient SQLite:** Node.js 22+ uses built-in `node:sqlite`; older Node runtimes can use optional `better-sqlite3` when native build tools are available.
- **Small, testable boundaries:** browser exploration, LLM reasoning, risk analysis, release gates, regression contracts, persistence, reporting, config, and observability each live behind separate modules.
- **CI/CD-ready observability:** run, scenario, bug, and risk events are exposed through `/api/metrics`. This can later be bridged to OpenTelemetry without rewriting the agent.
- **Security-first API posture:** `/api` routes can be protected with `QA_COPILOT_API_KEY`, JSON bodies are size-limited, baseline security headers are set, and private/local target URLs are blocked by default to reduce SSRF risk.

### Next-Level Feature: Product Risk Radar

Product Risk Radar is a product-intelligence layer for issues normal automation often misses: controls that click but produce no observable result, forms with inaccessible fields, authentication-like flows without obvious submit actions, placeholder navigation, and initial runtime/network instability.

Why it matters: SaaS quality failures are not only exceptions. Many revenue-impacting defects are silent UX failures: a primary CTA does nothing, a signup form lacks accessible labels, or a placeholder link ships in production. QA Copilot converts those signals into a risk score and actionable recommendations, giving product, QA, and engineering teams a triage-ready view instead of a raw automation log.

### Next-Level Feature: Bug-to-Regression Contracts

Bug-to-Regression Contracts turn autonomous findings into durable engineering assets. For every bug and product risk signal, QA Copilot can generate a Playwright spec that replays the discovered scenario, embeds the observed evidence as comments, and is ready to drop into CI.

Why it matters: most QA automation tools stop at a report. Real teams need a way to prevent the same defect from returning. This feature closes the loop from exploration to prevention: AI discovers the issue, QA Copilot writes the regression contract, and engineering can promote it into the test suite with minimal translation work.

### Next-Level Feature: Autonomous Release Gate

Autonomous Release Gate turns QA Copilot evidence into a deterministic `ship`, `warn`, or `block` decision for CI/CD. It evaluates product risk score, high-severity bugs, high-severity risks, failed scenario rate, and run completion status, then returns both human-readable rationale and a CI-friendly exit code.

Why it matters: release meetings often rely on scattered dashboards, partial test logs, and subjective judgment. QA Copilot now gives teams a transparent release policy that can be reviewed by humans and enforced by automation. The tradeoff is intentional: the gate is deterministic rather than LLM-driven so release governance remains auditable and repeatable.

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
| `GET` | `/api/runs/:id/risks` | Product Risk Radar signals for a run |
| `GET` | `/api/runs/:id/regression-contract` | JSON metadata plus generated Playwright regression spec |
| `GET` | `/api/runs/:id/regression-spec` | Raw downloadable Playwright spec |
| `GET` | `/api/runs/:id/release-gate` | Detailed autonomous release gate decision |
| `GET` | `/api/runs/:id/release-gate/ci` | CI-friendly release decision and exit code |
| `GET` | `/api/metrics` | In-process counters and recent QA events |
| `GET` | `/health` | Health check |

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | _(required)_ | OpenAI API key |
| `OPENAI_BASE_URL` | OpenAI default | Override for Azure / Ollama / LM Studio |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model for test generation |
| `PORT` | `3000` | HTTP server port |
| `QA_COPILOT_API_KEY` | _(unset)_ | Optional API key for `/api` routes; provide via `x-qa-copilot-api-key` or bearer auth |
| `LOG_HTTP_REQUESTS` | `true` | Structured HTTP request logging; defaults to false in tests |
| `DATABASE_PATH` | `./qa_copilot.db` | SQLite file path |
| `SCREENSHOTS_DIR` | `./screenshots` | Directory for screenshots |
| `HEADLESS` | `true` | Set to `false` for headed browser |
| `BROWSER_WIDTH` | `1280` | Viewport width |
| `BROWSER_HEIGHT` | `800` | Viewport height |
| `STEP_TIMEOUT_MS` | `10000` | Per-action Playwright timeout |
| `DISCOVERY_PAGE_LIMIT` | `4` | Maximum same-origin pages explored before scenario generation |
| `MAX_SCENARIOS` | `12` | Reserved scenario generation cap for future queue controls |
| `ALLOW_PRIVATE_TARGETS` | `false` | Allow testing localhost/private IP targets; enable only in trusted local environments |
| `RELEASE_GATE_MIN_RISK_SCORE` | `75` | Minimum Product Risk Radar score required to ship |
| `RELEASE_GATE_MAX_HIGH_SEVERITY_BUGS` | `0` | Maximum critical/high bugs allowed before blocking |
| `RELEASE_GATE_MAX_HIGH_SEVERITY_RISKS` | `2` | Maximum critical/high product risks before warning |
| `RELEASE_GATE_MAX_FAILED_SCENARIO_RATE` | `0.25` | Maximum failed scenario ratio before blocking |

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

Each Product Risk Radar signal includes:

- **Type** — `dead_interaction`, `accessibility_gap`, `conversion_friction`, `navigation_risk`, `technical_reliability`, or `coverage_gap`
- **Severity** — `critical` / `high` / `medium` / `low`
- **Evidence** — browser-observed proof for the signal
- **Recommendation** — product/engineering action to reduce the risk
- **Risk Score** — 0-100 run-level score shown in dashboards and reports

Each Autonomous Release Gate decision includes:

- **Decision** — `ship`, `warn`, or `block`
- **CI Exit Code** — `0` for ship/warn, `1` for block
- **Confidence** — 0-100 score based on checks, coverage, and finding volume
- **Checks** — observed values, thresholds, pass/fail state, and recommendations
- **Required Actions** — concrete release-readiness work for engineering and QA

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

