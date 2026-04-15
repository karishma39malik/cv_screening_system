import logging
import os
import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from api.routers import candidates, jobs, screenings
from database.connection import check_db_health
from shared.models import HealthResponse
from config.settings import settings

# ---- Structured logging setup ----
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),   # JSON logs for observability
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
)

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs on startup and shutdown."""
    # ---- STARTUP ----
    logger.info("system_startup", environment=settings.environment)

    # Ensure upload directories exist
    os.makedirs(f"{settings.upload_dir}/cvs", exist_ok=True)
    os.makedirs(f"{settings.upload_dir}/jds", exist_ok=True)
    os.makedirs(settings.log_dir, exist_ok=True)

    yield  # Application runs here

    # ---- SHUTDOWN ----
    logger.info("system_shutdown")


# ---- Create FastAPI app ----
app = FastAPI(
    title="CV Screening & Hiring Intelligence API",
    description="Agentic AI-powered CV screening system for HR",
    version="1.0.0",
    docs_url="/docs" if settings.environment == "development" else None,
    redoc_url=None,
    lifespan=lifespan,
)

# ---- Security middleware ----
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501"],  # Streamlit only
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["*"],
)

# ---- Route registration ----
app.include_router(jobs.router,       prefix="/api/v1/jobs",       tags=["Jobs"])
app.include_router(candidates.router, prefix="/api/v1/candidates", tags=["Candidates"])
app.include_router(screenings.router, prefix="/api/v1/screenings", tags=["Screenings"])


# ---- Health check endpoint ----
@app.get("/health", response_model=HealthResponse)
async def health_check():
    """
    System health check — used by Docker health checks and monitoring.
    """
    db_ok = await check_db_health()

    # Check Ollama availability
    import httpx
    ollama_ok = False
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags")
            ollama_ok = resp.status_code == 200
    except Exception:
        pass

    status = "healthy" if (db_ok and ollama_ok) else "degraded"

    return HealthResponse(
        status=status,
        database=db_ok,
        ollama=ollama_ok,
    )
