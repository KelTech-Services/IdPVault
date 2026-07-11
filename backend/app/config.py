from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """All runtime config via environment. See docker/.env.example."""

    database_url: str = "postgresql+psycopg://idpvault:idpvault@db:5432/idpvault"
    data_dir: str = "/data"
    master_key_file: str = "/secrets/master.key"
    log_level: str = "INFO"
    alert_webhook_url: str | None = None  # ntfy/Slack-compatible webhook
    admin_user: str = ""       # optional headless bootstrap; empty -> first-run wizard
    admin_password: str = ""   # set both only for automated/headless provisioning
    metrics_token: str | None = None  # set to enable /metrics for Prometheus

    model_config = {"env_prefix": "IDPVAULT_"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
