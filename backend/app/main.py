"""
main.py — FastAPI application entry point for AP Intelligence.

Startup sequence:
  1. Configure structured logging.
  2. Initialise the SQLite database.
  3. Register routes.
  4. Expose the ASGI app for uvicorn.
"""

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import APP_NAME, APP_ENV, APP_HOST, APP_PORT, LOG_LEVEL
from app.database import init_db, check_db_health
from app.routers.invoices import router as invoices_router

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


# ── Application factory ────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title=APP_NAME,
        description="AI-powered Accounts Payable invoice processing and reconciliation.",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ── Startup ────────────────────────────────────────────────────────────────
    @app.on_event("startup")
    async def on_startup() -> None:
        logger.info("Starting %s in [%s] mode …", APP_NAME, APP_ENV)
        try:
            init_db()
            logger.info("Application ready.")
        except Exception:
            logger.exception("Fatal: database initialisation failed.")
            raise

    # ── Routes ─────────────────────────────────────────────────────────────────

    app.include_router(invoices_router)

    @app.get("/health", tags=["System"])
    async def health_check():
        """
        Returns the overall health of the application.

        Checks:
        - Application status (always 'ok' if the process is running).
        - SQLite database connectivity.
        """
        db_health = check_db_health()

        overall = "ok" if db_health["status"] == "ok" else "degraded"

        payload = {
            "status": overall,
            "app": APP_NAME,
            "environment": APP_ENV,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": {
                "database": db_health,
            },
        }

        status_code = 200 if overall == "ok" else 503
        return JSONResponse(content=payload, status_code=status_code)

    # ── Static Files & Templates ───────────────────────────────────────────────
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    # ── Frontend Pages ─────────────────────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse, tags=["Frontend"])
    async def dashboard_page(request: Request):
        """Render the AP Intelligence Dashboard view."""
        return templates.TemplateResponse("index.html", {"request": request})

    @app.get("/process", response_class=HTMLResponse, tags=["Frontend"])
    async def live_run_page(request: Request):
        """Render the AP Intelligence Live Run processing view."""
        return templates.TemplateResponse("process.html", {"request": request})

    return app


# ── ASGI entry point ───────────────────────────────────────────────────────────
app = create_app()


# ── Dev runner ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=APP_HOST,
        port=APP_PORT,
        reload=True,
        log_level=LOG_LEVEL.lower(),
    )
