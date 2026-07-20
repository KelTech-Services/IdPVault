"""Alembic environment, wired to the app's own settings and metadata.

Runs in two ways:
- Programmatically at boot: app.models.db.init_db() passes the live engine
  connection via cfg.attributes["connection"].
- From the CLI during development (from backend/): alembic revision -m "..."
"""
from alembic import context
from sqlalchemy import create_engine

from app.config import get_settings
from app.models.db import Base

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection", None)
    if connection is not None:
        # Boot path: reuse the app engine's connection (init_db).
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return
    # CLI path: build our own engine from app settings.
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
