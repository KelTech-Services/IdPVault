#!/bin/sh
# IdPVault entrypoint - zero-config first boot.
#   * generates the master encryption key on a truly fresh install
#   * NEVER regenerates it (see guard below) - a new key would make existing
#     encrypted snapshots and credentials permanently unrecoverable
#   * fixes volume ownership, then drops root -> idpvault (uid 10001)
set -e

KEY_FILE="${IDPVAULT_MASTER_KEY_FILE:-/secrets/master.key}"
DATA_DIR="${IDPVAULT_DATA_DIR:-/data}"

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
    umask 277
    head -c 32 /dev/urandom > "$KEY_FILE"
    echo "Master key generated. BACK IT UP (see Docs -> Deployment & proxy):"
    echo "  docker cp idpvault:$KEY_FILE ./master.key.backup"
  fi

  # Ownership fixes: volumes created by docker (or files copied in over SSH)
  # arrive root/user-owned; the app runs as uid 10001. Read-only mounts are
  # tolerated (|| true) for setups that manage permissions themselves.
  chown idpvault "$(dirname "$KEY_FILE")" 2>/dev/null || true
  chown idpvault "$KEY_FILE" 2>/dev/null || true
  chmod 400 "$KEY_FILE" 2>/dev/null || true
  find "$DATA_DIR" ! -user idpvault -exec chown idpvault {} + 2>/dev/null || true

  exec setpriv --reuid=idpvault --regid=idpvault --clear-groups "$@"
fi

# Already non-root (custom `user:` in compose): assume the operator manages
# permissions; just run the app.
exec "$@"
