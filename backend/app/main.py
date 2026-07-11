import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import routes_audit, routes_auth, routes_backups, routes_dashboard, routes_health, routes_metrics, routes_restore, routes_settings, routes_tenants, routes_users
from app.config import get_settings
from app.core.scheduler import scheduler, load_tenant_jobs
from app.models.db import init_db

PUBLIC_API = {"/api/v1/auth/login", "/api/v1/auth/accept-invite"}


def bootstrap_admin() -> None:
    """First run: create the admin account from env if no users exist."""
    from app.core.security import hash_password
    from app.models.db import SessionLocal, User
    s = get_settings()
    with SessionLocal() as db:
        if db.query(User).count() == 0:
            db.add(User(username=s.admin_user, email="", role="admin", is_active=True,
                        password_hash=hash_password(s.admin_password)))
            db.commit()
            logging.getLogger(__name__).info("bootstrap admin user %r created", s.admin_user)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    init_db()
    bootstrap_admin()
    scheduler.start()
    load_tenant_jobs()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="IdPVault", version="0.4.2", lifespan=lifespan)


@app.middleware("http")
async def session_auth(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/") or path in PUBLIC_API:
        return await call_next(request)  # static UI + login endpoints are public; APIs are not
    from app.core.security import resolve_session
    from app.models.db import SessionLocal
    with SessionLocal() as db:
        user = resolve_session(db, request.cookies.get("idpvault_session", ""))
    if user is None:
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    request.state.user = {"username": user.username, "role": user.role, "email": user.email}
    return await call_next(request)


app.include_router(routes_health.router)
app.include_router(routes_auth.router, prefix="/api/v1")
app.include_router(routes_tenants.router, prefix="/api/v1")
app.include_router(routes_backups.router, prefix="/api/v1")
app.include_router(routes_users.router, prefix="/api/v1")
app.include_router(routes_dashboard.router, prefix="/api/v1")
app.include_router(routes_restore.router, prefix="/api/v1")
app.include_router(routes_audit.router, prefix="/api/v1")
app.include_router(routes_metrics.router)
app.include_router(routes_settings.router, prefix="/api/v1")
app.mount("/", StaticFiles(directory="frontend", html=True), name="ui")
