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


def probe_target(db_url: str, timeout: int = 30) -> dict:
    """Full-DR restore preflight: connect to the target and classify it.
    Returns {"version": str, "kind": "authentik" | "empty", "tables": int}.
    Raises with a plain-English reason when the target is unreachable or is
    some OTHER application's database (wrong-URL guard - a restore would
    destroy it)."""
    q = ("SELECT current_setting('server_version') || '|' || "
         "count(*) || '|' || "
         "count(*) FILTER (WHERE table_name LIKE 'authentik_core_%') "
         "FROM information_schema.tables WHERE table_schema='public'")
    proc = subprocess.run(["psql", "--no-psqlrc", "-t", "-A", "-c", q, db_url],
                          capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError("cannot connect to the Full-DR database: "
                           f"{proc.stderr.decode(errors='replace')[:300]}")
    version, total, ak = proc.stdout.decode(errors="replace").strip().split("|")
    total, ak = int(total), int(ak)
    if total == 0:
        return {"version": version, "kind": "empty", "tables": 0}
    if ak == 0:
        raise RuntimeError(
            f"the Full-DR database has {total} tables but none of them are "
            "Authentik's - this looks like a DIFFERENT application's database. "
            "Refusing: a restore would destroy it. Check the Full-DR Postgres "
            "URL in the tenant settings.")
    return {"version": version, "kind": "authentik", "tables": total}


def psql_restore(db_url: str, sql: bytes, timeout: int = 1800,
                 progress_cb=None) -> dict:
    """Apply a plain-SQL dump with psql. ON_ERROR_STOP + --single-transaction
    make it atomic: if ANYTHING fails, the whole restore rolls back and the
    target database is left exactly as it was. The dump is fed through stdin
    in chunks so progress_cb(bytes_done, bytes_total) can report real percent.
    Returns {"bytes": n, "duration_ms": n}."""
    import os
    import tempfile
    import time as _time
    t0 = _time.monotonic()
    cmd = ["psql", "--no-psqlrc", "--single-transaction",
           "--set", "ON_ERROR_STOP=1", "--quiet", db_url]
    # client_min_messages=warning: a --clean dump against an empty database
    # emits one NOTICE per skipped DROP - enough to fill a stderr PIPE and
    # deadlock the stdin feed. Notices go nowhere; stderr goes to a temp file
    # (never a pipe) so it can't block regardless.
    env = dict(os.environ, PGOPTIONS="-c client_min_messages=warning")
    with tempfile.TemporaryFile() as errf:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=errf, env=env)
        total, chunk = len(sql), 1 << 20   # 1 MB chunks
        try:
            for i in range(0, total, chunk):
                proc.stdin.write(sql[i:i + chunk])
                if progress_cb:
                    try:
                        progress_cb(min(i + chunk, total), total)
                    except Exception:
                        pass   # progress is cosmetic, never fail the restore
            proc.stdin.close()
            rc = proc.wait(timeout=timeout)
        except BrokenPipeError:
            proc.wait(timeout=30)
            rc = proc.returncode or 1
        except subprocess.TimeoutExpired:
            proc.kill()
            raise RuntimeError(f"psql restore timed out after {timeout}s - the "
                               "transaction was rolled back, the database is unchanged")
        if rc != 0:
            errf.seek(0)
            err = errf.read().decode(errors="replace")[:500]
            raise RuntimeError(f"psql exited {rc} - the restore transaction was "
                               f"ROLLED BACK, the database is unchanged: {err}")
    return {"bytes": total, "duration_ms": int((_time.monotonic() - t0) * 1000)}
