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

**v0.5.0** — identity backup: opt-in per-tenant users + group memberships + app assignments
backup (separate schedule/storage/retention), provenance-aware model (group-inherited vs
direct), header-driven adaptive Okta rate limiting with configurable reserve headroom,
measured duration estimate + cadence recommendation, dry-run identity restore preview.
Okta config coverage completed (profile mappings, user types, per-app schemas).

**v0.5.1** — identity restore APPLY (write path): create-only, additive, idempotent restore
of missing users + memberships + assignments, with full natural-key ID remapping (login /
group name / app label) so recreated-object id changes don't break edges. Per-object report,
throttled, admin-only, explicit confirm. Okta + Authentik.

**v0.6.0** — zero-config first-run: browser setup wizard creates the admin on fresh install
(no admin creds required in the stack YAML; env-var bootstrap still supported for headless).
Self-service profile: change own password, enable/disable TOTP MFA (stdlib TOTP + QR
enrollment, secrets encrypted at rest). MFA enforced at sign-in.

**v0.7.0–0.7.3** — security & provider hardening: login brute-force lockout, security
headers, HTTPS-aware secure cookies, self-health stale-backup alerting; reverse-proxy
hardening (canonical Public URL, `X-Forwarded-Proto`, optional Host enforcement); Auth0
switched to OAuth2 client-credentials (auto-minted Management API tokens); provider-driven
Add-Tenant form (per-provider fields).

**v0.7.4–0.7.5** — Auth0 hardening + restore: resilient Auth0 export (single-fetch
endpoints, skip feature-gated/deprecated ones); **Auth0 config restore-apply** (clients,
connections, resource servers, roles, rules); per-adapter restore ordering + never-restore.

## v0.7 — next

- Restore apply for Okta **config** adapter (Auth0 shipped in v0.7.5)
- Optional profile-revert for existing users (identity restore currently create-only)
- Background job queue for long identity backups/restores (currently synchronous)
- Identity events/diff lane
- Restore-run history viewer in UI
- Automated pg_dump *restore* (currently dump is captured/downloadable, not auto-applied)
- Alembic migrations (before any non-additive schema change)

## Later / SaaS-track
- Alembic migrations (required before any column change to existing tables)
- Terraform / blueprint export
- Public website, subscription billing, branded org migration
