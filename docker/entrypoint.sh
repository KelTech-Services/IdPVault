#!/bin/sh
# IdPVault entrypoint - zero-config first boot.
#   * generates the master encryption key on a truly fresh install
#   * NEVER regenerates it (see guard below) - a new key would make existing
#     encrypted snapshots and credentials permanently unrecoverable
#   * fixes volume ownership, then drops root -> PUID:PGID (default 10001,
#     the idpvault user). Set PUID/PGID to run as your own ids instead -
#     useful when bind mounts are owned by your NAS user.
set -e

KEY_FILE="${IDPVAULT_MASTER_KEY_FILE:-/secrets/master.key}"
DATA_DIR="${IDPVAULT_DATA_DIR:-/data}"
PUID="${PUID:-10001}"
PGID="${PGID:-10001}"

if [ "$(id -u)" = "0" ]; then
  mkdir -p "$DATA_DIR" "$(dirname "$KEY_FILE")" 2>/dev/null || true

  if [ ! -f "$KEY_FILE" ]; then
    # NEVER-REGENERATE GUARD: key missing but data present => refuse to boot.
    if [ -n "$(find "$DATA_DIR" -mindepth 1 -maxdepth 1 2>/dev/null | head -1)" ]; then
      echo "FATAL: $KEY_FILE is missing but $DATA_DIR already contains data." >&2
      echo "Restore your master.key backup into the secrets volume. Generating a" >&2
      echo "new key would make all existing snapshots and stored credentials" >&2
      echo "permanently unrecoverable, so IdPVault refuses to start instead." >&2
      exit 1
    fi
    echo "First boot: generating master encryption key at $KEY_FILE"
    # umask is scoped to a subshell: pre-1.1.3 it leaked into the app process,
    # so every dir the app created before the first restart was 0500
    # (unwritable by its own owner) and the first backups failed.
    ( umask 277; head -c 32 /dev/urandom > "$KEY_FILE" )
    echo "Master key generated. BACK IT UP (see Docs -> Deployment & proxy):"
    echo "  docker cp idpvault:$KEY_FILE ./master.key.backup"
  fi

  # Ownership fixes: volumes created by docker (or files copied in over SSH)
  # arrive root/user-owned; the app runs as $PUID. Read-only mounts are
  # tolerated (|| true) for setups that manage permissions themselves.
  chown "$PUID:$PGID" "$(dirname "$KEY_FILE")" 2>/dev/null || true
  chown "$PUID:$PGID" "$KEY_FILE" 2>/dev/null || true
  chmod 400 "$KEY_FILE" 2>/dev/null || true
  find "$DATA_DIR" ! -user "$PUID" -exec chown "$PUID:$PGID" {} + 2>/dev/null || true
  # Self-heal dirs/files left owner-unwritable by the pre-1.1.3 umask leak.
  find "$DATA_DIR" ! -perm -u+w -exec chmod u+w {} + 2>/dev/null || true

  umask 022
  exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
fi

# Already non-root (custom `user:` in compose): assume the operator manages
# permissions; just run the app.
exec "$@"
