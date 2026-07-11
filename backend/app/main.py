import base64
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

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


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    """HTTP Basic auth on everything except the container healthcheck."""
    if request.url.path == "/healthz":
        return await call_next(request)
    settings = get_settings()
    header = request.headers.get("Authorization", "")
    ok = False
    if header.startswith("Basic "):
        try:
            user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
            ok = secrets.compare_digest(user, settings.admin_user) and \
                 secrets.compare_digest(pw, settings.admin_password)
        except Exception:
            ok = False
    if not ok:
        return JSONResponse({"detail": "unauthorized"}, status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="IdPVault"'})
    return await call_next(request)


app.include_router(routes_health.router)
app.include_router(routes_tenants.router, prefix="/api/v1")
app.include_router(routes_backups.router, prefix="/api/v1")
app.mount("/", StaticFiles(directory="frontend", html=True), name="ui")
