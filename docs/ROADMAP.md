# IdPVault Roadmap

## v0.2 — accounts, settings, real UI depth

**Authentication & accounts**
- Real login page (session cookies, hashed passwords in Postgres) replacing HTTP Basic
- First-run admin setup flow
- User management: admins create accounts; roles: `admin` (full control) and
  `user` (read-only: view tenants, snapshots, diffs, history — no mutations)
- Invite flow: new account -> one-time emailed setup link where the invitee sets their password

**Settings page**
- SMTP configuration (host, port, TLS, credentials encrypted at rest, from-address, test-send button)
- Alert webhook (ntfy / Slack-compatible) for drift and backup-failure notifications
- Default schedule / retention for new tenants
- Master key + app health status

**Tenant experience**
- Tenant detail view: backup history with status, per-snapshot object counts, drift badges,
  last-run and next-scheduled times
- Drift alerts actually delivered (webhook + SMTP)

**Backup & restore depth**
- Restore with dry-run preview (object-level, dependency-aware ordering)
- Authentik "full DR" mode: optional tenant field for its Postgres URL; take an encrypted
  `pg_dump` alongside each config snapshot (bind mounts / SECRET_KEY remain host-backup scope)
- Audit log viewer in UI
- Prometheus metrics endpoint

## Later / SaaS-track
- Cross-tenant config promotion (prod -> preview), tenant cloning
- Terraform / blueprint export
- Public website, subscription billing, move repo to branded org
