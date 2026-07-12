from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict:
    from app.main import app
    return {"status": "ok", "version": app.version}
