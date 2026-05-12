"""QA Copilot — FastAPI application entry point."""
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from api.routes import sessions, reports, cicd
from api.routes import (
    templates as templates_router,
    schedules as schedules_router,
    environments as environments_router,
    variables as variables_router,
    credentials as credentials_router,
    notifications as notifications_router,
    integrations as integrations_router,
    webhooks as webhooks_router,
    analytics as analytics_router,
)
from db.database import init_db

logging.basicConfig(
    level="INFO",
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("QA Copilot started. DB initialized.")
    try:
        from agent.scheduler import start_scheduler
        await start_scheduler()
    except Exception as exc:
        logger.warning("Scheduler startup error (non-fatal): %s", exc)
    yield
    try:
        from agent.scheduler import stop_scheduler
        stop_scheduler()
    except Exception:
        pass
    logger.info("QA Copilot shutting down.")


app = FastAPI(
    title="QA Copilot",
    description=(
        "Autonomous AI-powered QA testing agent. "
        "Explores web applications like a human, finds bugs, generates reports."
    ),
    version="1.2.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="dashboard/static"), name="static")
templates = Jinja2Templates(directory="dashboard/templates")

app.include_router(sessions.router,      prefix="/api")
app.include_router(reports.router,       prefix="/api")
app.include_router(cicd.router,          prefix="/api")
app.include_router(templates_router.router,     prefix="/api")
app.include_router(schedules_router.router,     prefix="/api")
app.include_router(environments_router.router,  prefix="/api")
app.include_router(variables_router.router,     prefix="/api")
app.include_router(credentials_router.router,   prefix="/api")
app.include_router(notifications_router.router, prefix="/api")
app.include_router(integrations_router.router,  prefix="/api")
app.include_router(webhooks_router.router,      prefix="/api")
app.include_router(analytics_router.router,     prefix="/api")


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "qa-copilot", "version": "1.2.0"}


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error": str(exc)},
    )
