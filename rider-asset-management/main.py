from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import close_db, connect_db
from cron.weekly_settlement import setup_cron_jobs
from routes.admin import router as admin_router
from routes.assets import router as assets_router
from routes.issuances import router as issuances_router
from routes.reports import router as reports_router
from routes.rider import router as rider_router
from routes.settlement import router as settlement_router
from routes.vendors import router as vendors_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# APScheduler instance
# ---------------------------------------------------------------------------
scheduler = AsyncIOScheduler()


# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- STARTUP ----
    logger.info("Connecting to MongoDB…")
    await connect_db()
    logger.info("MongoDB connected.")

    # Register and start cron jobs
    setup_cron_jobs(scheduler)
    scheduler.start()
    logger.info("APScheduler started.")

    yield

    # ---- SHUTDOWN ----
    scheduler.shutdown(wait=False)
    logger.info("APScheduler stopped.")
    await close_db()
    logger.info("MongoDB disconnected.")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Fairdeal Rider Asset Management & Settlement System",
    description=(
        "Manages asset issuance to delivery riders, weekly deduction settlements, "
        "vendor liability tracking, and audit logging."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — adjust origins for production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Register routers
# ---------------------------------------------------------------------------

app.include_router(assets_router)
app.include_router(issuances_router)
app.include_router(settlement_router)
app.include_router(vendors_router)
app.include_router(admin_router)
app.include_router(rider_router)
app.include_router(reports_router)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health", tags=["Health"])
async def health_check() -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "rider-asset-management"})


@app.get("/", tags=["Root"])
async def root() -> JSONResponse:
    return JSONResponse(
        {
            "service": "Fairdeal Rider Asset Management & Settlement System",
            "version": "1.0.0",
            "docs": "/docs",
            "redoc": "/redoc",
        }
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
