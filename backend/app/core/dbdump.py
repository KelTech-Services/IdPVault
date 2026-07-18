"""Optional full-DR: pg_dump of a self-hosted IdP database, encrypted at rest.

Uses the pg_dump binary in the image. Best-effort: a dump failure logs and is
recorded in the manifest but never fails the config backup.
"""
import logging
import subprocess

log = logging.getLogger(__name__)


# Ephemeral/history tables excluded in "smaller dumps" mode. Measured on a real
# instance: sessions + task logs + events were 98%+ of the dump; none carry
# durable configuration. Schema always stays in the dump, so restores boot
# clean - users sign in again, and historical events/task logs are not restored.
# Non-matching patterns are ignored by pg_dump (no --strict-names), so this set
# is safe across Authentik versions old and new.
_EPHEMERAL_TABLE_PATTERNS = [
    "authentik_events_*",                       # event history + notifications
    "authentik_core_session*",                  # login sessions (2024.x+)
    "authentik_core_authenticatedsession*",     # login sessions (older)
    "authentik_providers_proxy_proxysession*",  # proxy runtime sessions
    "authentik_tasks_*",                        # background task queue + logs
    "django_session*",                          # legacy django sessions
]


def pg_dump(db_url: str, timeout: int = 300, exclude_events: bool = False) -> bytes:
    """Return a plain-SQL dump. Raises on failure. exclude_events skips the ROW
    DATA of ephemeral and history tables (sessions, task logs, event history) -
    the schema stays in the dump so restores boot clean."""
    cmd = ["pg_dump", "--no-owner", "--no-privileges", "--clean", "--if-exists"]
    if exclude_events:
        cmd += ["--exclude-table-data=%s" % p for p in _EPHEMERAL_TABLE_PATTERNS]
    cmd.append(db_url)
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"pg_dump exited {proc.returncode}: "
                           f"{proc.stderr.decode(errors='replace')[:300]}")
    return proc.stdout
