import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import routes_audit, routes_auth, routes_backups, routes_dashboard, routes_health, routes_identity, routes_license, routes_metrics, routes_orgs, routes_restore, routes_settings, routes_tenants, routes_users
from app.config import get_settings
from app.core.scheduler import scheduler, load_tenant_jobs
from app.models.db import init_db

PUBLIC_API = {"/api/v1/auth/login", "/api/v1/auth/accept-invite",
              "/api/v1/auth/status", "/api/v1/auth/setup", "/api/v1/auth/forgot"}


def bootstrap_admin() -> None:
    """First run: create the admin account from env if no users exist."""
    from app.core.security import hash_password
    from app.models.db import SessionLocal, User
    s = get_settings()
    if not (s.admin_user and s.admin_password):
        return  # no headless creds -> first-run setup wizard creates the admin
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
    from app.core.crypto import verify_master_key_matches_db
    verify_master_key_matches_db()   # never boot with a key that can't decrypt existing data
    bootstrap_admin()
    scheduler.start()
    load_tenant_jobs()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="IdPVault", version="1.2.2", lifespan=lifespan)


@app.middleware("http")
async def host_guard(request: Request, call_next):
    # Reject unexpected Host headers when strict enforcement is enabled (health/metrics exempt).
    if request.url.path not in ("/healthz", "/metrics"):
        from app.core import deploy
        if not deploy.host_allowed(request):
            return JSONResponse({"detail": "host not allowed"}, status_code=400)
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; base-uri 'self'; frame-ancestors 'none'")
    return resp


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
    request.state.user = {"id": user.id, "username": user.username, "role": user.role,
                          "email": user.email, "org_id": user.org_id}
    return await call_next(request)


app.include_router(routes_health.router)
app.include_router(routes_auth.router, prefix="/api/v1")
app.include_router(routes_tenants.router, prefix="/api/v1")
app.include_router(routes_backups.router, prefix="/api/v1")
app.include_router(routes_users.router, prefix="/api/v1")
app.include_router(routes_dashboard.router, prefix="/api/v1")
app.include_router(routes_restore.router, prefix="/api/v1")
app.include_router(routes_audit.router, prefix="/api/v1")
app.include_router(routes_identity.router, prefix="/api/v1")
app.include_router(routes_metrics.router)
app.include_router(routes_settings.router, prefix="/api/v1")
app.include_router(routes_license.router, prefix="/api/v1")
app.include_router(routes_orgs.router, prefix="/api/v1")
class UIStaticFiles(StaticFiles):
    """Serve HTML with no-cache so browsers revalidate on every load (304s still
    apply via ETag). A stale cached index.html against a newer API once made a
    just-shipped UI fix invisible - never let the shell be cached."""
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if str(response.headers.get("content-type", "")).startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/", UIStaticFiles(directory="frontend", html=True), name="ui")
