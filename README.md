# IdPVault

Self-hosted backup, drift detection, and restore for **Authentik**, **Okta**, and **Auth0** tenants.

Point IdPVault at your identity providers and it takes scheduled, encrypted snapshots of every
configuration object — apps, flows, policies, groups, mappings, and more. Browse snapshot history,
diff any two points in time, get alerted on config drift, and restore objects when something breaks.

## Features

- Scheduled + on-demand encrypted backups (AES-256-GCM, per-tenant envelope keys)
- Providers: Authentik, Okta, Auth0 (pluggable adapter interface)
- Users & Access backup (opt-in): users, group memberships, and provenance-aware app assignments
- Adaptive Okta rate limiting (auto-learns limits; configurable reserve headroom)
- Snapshot-to-snapshot diff, drift detection, and a per-object change events feed
- Restore engine: dry-run preview, dependency-ordered apply (Authentik), per-object
  restore reports
- Multi-user: session login, admin / read-only roles, email invites, SMTP settings
- Dashboard: coverage, unbacked-changes (live IdP event polling), storage stats
- Snapshot object browser — inspect any object in any snapshot
- Retention policies per tenant; audit log with viewer
- Alerts on drift or failed backups: webhook (ntfy / Slack) + email
- Prometheus metrics endpoint (set IDPVAULT_METRICS_TOKEN to enable)
- Backups queue and run one at a time by default, safe for modest hosts (set
  IDPVAULT_BACKUP_WORKERS to run more in parallel); schedules run in your org
  timezone, DST-aware

## Quick deploy (Docker Compose / Portainer stack)

See `docker/compose.example.yaml`. Single app image + Postgres, no other dependencies.

## Repo layout

- `backend/` — FastAPI application
- `frontend/` — web UI (planned)
- `docker/` — Dockerfile, example compose stack, env template
- `docs/` — architecture and operations notes
- `.gitea/workflows/` — CI: build image, push to registry

## Status

v0.5.0 shipped. See `docs/USER_GUIDE.md` for setup/usage and `docs/ROADMAP.md` for shipped versions and what's next.

## What a backup contains — and what it doesn't

IdPVault backs up your IdP's **configuration** via its API: applications, providers, flows,
stages, policies, groups, property mappings, and the rest of the objects listed per provider.
Snapshots are encrypted, versioned, and diffable, and support selective config restore.

**This is not a full disaster-recovery backup.** Two things to understand before you rely on it:

1. **Secrets are redacted by the IdP, not by us.** Identity providers deliberately never return
   OAuth2 client secrets, certificate private keys, SMTP passwords, or signing keys through
   their export/read APIs. A restored object may therefore come back with its secret missing —
   you will need to re-enter or rotate those secrets after a restore. This is true of every
   backup product in this space, for Okta and Auth0 as much as Authentik.

2. **Self-hosted IdPs have state outside the API.** For self-hosted Authentik, a true
   bare-metal recovery additionally needs, backed up by your own infrastructure tooling:
   - a `pg_dump` of the Authentik Postgres database (the actual source of truth),
   - its bind mounts (`/data`, `/certs`, `/custom-templates`, `/media`),
   - the compose file / environment, especially `AUTHENTIK_SECRET_KEY` — without the same
     secret key, a restored database cannot decrypt the secrets it holds.

   A planned "full DR" mode (see `docs/ROADMAP.md`) will optionally capture an encrypted
   `pg_dump` alongside config snapshots for self-hosted tenants.

Use IdPVault for what it is: configuration versioning, drift detection, and config-level
restore. Pair it with host-level backups of your self-hosted IdP for full disaster recovery.


## Licensing & tiers

IdPVault is source-available under the **Business Source License 1.1** (see
`LICENSE`): you can run it in production for your own organization; offering it
to third parties as a hosted/managed service requires a commercial agreement.
The code converts to Apache 2.0 on the Change Date.

The app itself is open-core:

- **Community (free, no key needed):** 1 tenant, full config backup, drift
  detection & events, alerts, and config restore.
- **Business:** 4 tenants, unlimited users, and Users & Access backup & restore
  (users, group memberships, app assignments).
- **MSP** (upcoming): tenant add-ons whenever you need them, client orgs, and
  org-scoped users. Flat published pricing at https://idpvault.com.

License keys are Ed25519-signed tokens verified **entirely offline** against a
public key embedded in the app — IdPVault never phones home and sends no
telemetry. Install/manage keys in **Settings → License**. If a license expires
(after a 3-day grace window) or is removed, nothing is deleted: your oldest
tenant stays fully operational, other tenants keep all their data and snapshots
but pause backup/restore, and identity features pause — everything resumes as
soon as a valid key is installed. Renewal keys can be installed early; their
term extends from the previous expiry.
