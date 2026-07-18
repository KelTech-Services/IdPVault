"""Optional full-DR: pg_dump of a self-hosted IdP database, encrypted at rest.

Uses the pg_dump binary in the image. Best-effort: a dump failure logs and is
recorded in the manifest but never fails the config backup.
"""
import logging
import subprocess

log = logging.getLogger(__name__)


def pg_dump(db_url: str, timeout: int = 300, exclude_events: bool = False) -> bytes:
    """Return a plain-SQL dump. Raises on failure. exclude_events skips the ROW
    DATA of Authentik's event-log tables (authentik_events_*) - the schema stays
    in the dump so restores boot clean, but historical events are not included."""
    cmd = ["pg_dump", "--no-owner", "--no-privileges", "--clean", "--if-exists"]
    if exclude_events:
        cmd.append("--exclude-table-data=authentik_events_*")
    cmd.append(db_url)
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"pg_dump exited {proc.returncode}: "
                           f"{proc.stderr.decode(errors='replace')[:300]}")
    return proc.stdout
