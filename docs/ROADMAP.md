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

**v0.7.4–0.7.22** — the restore engine, completed and battle-tested live on all three
providers: Auth0 config restore-apply (self-healing writes, 429 retry); **Okta config
restore-apply including apps** (recreated apps get regenerated credentials, flagged);
**Authentik restore completed** (applications & policy bindings, id remapping so
bindings follow recreated objects, converging comparisons); hybrid id/natural-key
matching (renames AND recreates, no duplicate creates); per-item restore selection
with checkboxes; unsupported objects visible in plans; drift-detection fix for
identity-enabled tenants; one consolidated email per backup with the change list.

**v0.8.0** — open-core licensing: offline Ed25519-signed license keys (no phone-home),
free Community tier (1 tenant, config backup/restore), paid unlocks more tenants +
identity backup & restore. Strict-but-non-destructive downgrade with a 3-day grace
window; oldest tenant stays fully live on free. Settings → License UI with expiry
countdown; server-side gating everywhere; repo licensed under BSL 1.1.

**v0.8.1–0.8.4** — polish & guardrails: license terms finalized (annual keys, renewal
extends from previous expiry, single-admin free tier); in-app Docs section (9 topic
pages, visible to all users); 22-test smoke suite (crypto, license verification,
restore matching, auth, alerts) wired into CI so a failing test blocks the image from
reaching the registry; user-facing copy style pass; "Set password now" option when
creating users (no SMTP required).

**v0.8.5–0.8.13** — UX & platform: clickable unbacked-changes breakdown, Users & Access
restore UI matching config restore, timezone-aware schedule pickers with per-user
12/24h preference, serial backup queue (one tenant at a time), light/dark themes,
zero-config deployment (auto-generated master.key with never-regenerate guard, named
volumes, generated DB password), Auth0 Users & Access support, VChart dashboard
(events / runs / storage charts) and a design pass.

**v0.8.14–0.8.15** — public GitHub move: full history published at
KelTech-Services/IdPVault, GHCR image, Actions CI (tests gate builds, write-once
version tags, auto-releases from CHANGELOG), CodeQL/secret-scanning/dependabot at
zero open alerts, issue forms, wiki mirroring in-app docs.

**v0.9.0** — MSP tier (license `msp` feature): client orgs with light CRM fields
(contact, notes, billing memo/cadence, renewal date) and an Orgs admin page;
tenants and users assignable to orgs; org-scoped roles org_admin (backup/browse/
restore within own org) and org_viewer (read-only, "contact your MSP administrator"
messaging); server-side scoping on every endpoint with 404 for out-of-org tenants;
dashboard renewals card (next 60 days + overdue); MSP doc page gated by license;
/auth/me exposes the feature list for UI gating. Additive-only schema (no
migration needed); admin/user roles and existing installs unchanged.

**v1.0.0** — milestone release: full product surface shipped and validated in
production (config + Users & Access backup/restore for Authentik, Okta, Auth0;
MSP orgs and scoped roles; zero-config deploy; offline licensing). No functional
changes from 0.9.0.

## Planned

### Go-to-market

- Marketing site (idpvault.com) + Stripe checkout, flat public pricing.
  MSP mechanics: license minted from the Stripe subscription state (subscription =
  source of truth, key = signed receipt). Tenant count is a Stripe quantity;
  customers add tenants any time via the Stripe customer portal ("Manage license"
  link in Settings → License) — quantity change prorates, webhook mints a
  replacement key with the same expiry and higher max_tenants, emailed instantly.
  No sales calls, flat published pricing. Sold terms are annual only; on MSP
  expiry org-scoped users keep read-only sign-in and write actions pause with
  license messaging (existing grace/downgrade logic).
- Later MSP upsells (post-1.0): per-org alert routing (client's own
  webhook/email), client-facing monthly backup report per org.


## Next

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
