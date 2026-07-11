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
in any snapshot), Prometheus metrics endpoint (token-guarded, IDPVAULT_METRICS_TOKEN).

## v0.5 — next

- Tenant cloning / cross-tenant promotion (restore engine applied to a different
  target tenant; Authentik→Authentik first)
- Authentik "full DR" mode: optional per-tenant Postgres URL; encrypted pg_dump
  alongside config snapshots (requires postgres-client in image)
- Restore apply for Okta and Auth0 adapters
- Restore-run history viewer in UI

## Later / SaaS-track
- Alembic migrations (required before any column change to existing tables)
- Terraform / blueprint export
- Public website, subscription billing, branded org migration
