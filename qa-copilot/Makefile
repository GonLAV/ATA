.PHONY: install install-browsers run dev test-import lint clean help

PYTHON := python3
VENV := .venv
PIP := $(VENV)/bin/pip
UV := uv

help:
	@echo ""
	@echo "  QA Copilot — Autonomous AI QA Testing Agent"
	@echo ""
	@echo "  make install          Install Python deps + Playwright browsers"
	@echo "  make run              Start the server (production)"
	@echo "  make dev              Start the server (auto-reload)"
	@echo "  make test-import      Verify all modules import cleanly"
	@echo "  make lint             Run ruff linter"
	@echo "  make export-junit     Export last session as JUnit XML"
	@echo "  make clean            Remove cache and DB files"
	@echo ""

install:
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt
	$(PYTHON) -m playwright install chromium --with-deps
	@echo ""
	@echo "✓ Installation complete."
	@echo "  Copy .env.example to .env and set ANTHROPIC_API_KEY."
	@echo ""

install-browsers:
	$(PYTHON) -m playwright install chromium --with-deps

run:
	PYTHONPATH=. $(PYTHON) -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 2

dev:
	PYTHONPATH=. $(PYTHON) -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload --log-level info

test-import:
	PYTHONPATH=. $(PYTHON) -c "
from config import settings
from db.models import Session, Bug, TestRun
from db.database import init_db
from ai.client import get_client
from ai.prompts import SYSTEM_PROMPT
from browser.driver import BrowserDriver
from browser.interceptor import PageInterceptor
from personas.engine import PersonaEngine, PERSONAS
from agent.explorer import PersonaExplorer
from agent.coordinator import SessionCoordinator
from agent.reporter import generate_report
from agent.validator import ActionValidator
from cicd.exporter import to_junit_xml, to_badge_json, to_sarif
print('✓ All modules imported successfully')
print(f'  Personas: {[p[\"name\"] for p in PERSONAS]}')
print(f'  Model: {settings.model}')
"

lint:
	$(PYTHON) -m ruff check . --select E,F,W,I --ignore E501

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	rm -f qa_copilot.db
	rm -rf screenshots/

export-junit:
	@if [ -z "$(SESSION)" ]; then \
	  echo "Usage: make export-junit SESSION=<session-id>"; \
	else \
	  PYTHONPATH=. $(PYTHON) -m cicd.exporter --session $(SESSION) --format junit --out results.xml && echo "Written: results.xml"; \
	fi
