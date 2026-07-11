import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import routes_backups, routes_health, routes_tenants
from app.config import get_settings
from app.core.scheduler import scheduler, load_tenant_jobs
from app.models.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    init_db()
    scheduler.start()
    load_tenant_jobs()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="IdPVault", version="0.1.0", lifespan=lifespan)
app.include_router(routes_health.router)
app.include_router(routes_tenants.router, prefix="/api/v1")
app.include_router(routes_backups.router, prefix="/api/v1")
