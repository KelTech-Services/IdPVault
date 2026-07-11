# IdPVault Roadmap

Goal: feature parity with commercial IdP backup/recovery products, self-hosted,
single-image deploy, encryption-first. Providers: Authentik, Okta, Auth0.

## Shipped

**v0.1** — backup core: encrypted snapshots (envelope AES-256-GCM), provider adapters
(Authentik/Okta/Auth0 export), cron scheduler, retention, diff engine, minimal UI.

**v0.2** — multi-user app: session login, admin/user roles (server-enforced),
invite flow with one-time links, SMTP + settings, tenant editing with token rotation.

**v0.3** — recovery & visibility: restore engine (dry-run preview + apply for
Authentik, dependency-ordered, per-object restore reports), events feed from backup
diffs, dashboard cards (coverage / unbacked changes / storage), backup run tracking
incl. failures, volatile-field normalization so drift = config drift only.

**v0.4** — alerts & reach: drift + backup-failure alerts (webhook ntfy/Slack + email
to admins), audit log viewer, snapshot object browser (browse/search/view any object
in any snapshot), Prometheus metrics endpoint (token-guarded, IDPVAULT_METRICS_TOKEN),
tenant clone/promote (restore a snapshot into a different same-provider tenant),
Authentik full-DR mode (optional per-tenant Postgres URL -> encrypted pg_dump beside
each config snapshot; needs postgresql-client, in image). Additive column-migration
guard added (ALTER ... ADD COLUMN IF NOT EXISTS) for post-table schema additions.

## v0.5 — next

- Restore apply for Okta and Auth0 adapters
- Restore-run history viewer in UI
- Automated pg_dump *restore* (currently dump is captured/downloadable, not auto-applied)
- Alembic migrations (before any non-additive schema change)

## Later / SaaS-track
- Alembic migrations (required before any column change to existing tables)
- Terraform / blueprint export
- Public website, subscription billing, branded org migration
