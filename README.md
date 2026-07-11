# IdPVault

Self-hosted backup, drift detection, and restore for **Authentik**, **Okta**, and **Auth0** tenants.

Point IdPVault at your identity providers and it takes scheduled, encrypted snapshots of every
configuration object — apps, flows, policies, groups, mappings, and more. Browse snapshot history,
diff any two points in time, get alerted on config drift, and restore objects when something breaks.

## Features

- Scheduled + on-demand encrypted backups (AES-256-GCM, per-tenant envelope keys)
- Providers: Authentik, Okta, Auth0 (pluggable adapter interface)
- Snapshot-to-snapshot diff and drift detection
- Restore: dry-run first, object-level precision (roadmap: dependency tracking)
- Retention policies per tenant
- Audit log of every action
- Webhook alerts (ntfy / Slack) on drift or failed backups
- Prometheus metrics endpoint

## Quick deploy (Docker Compose / Portainer stack)

See `docker/compose.example.yaml`. Single app image + Postgres, no other dependencies.

## Repo layout

- `backend/` — FastAPI application
- `frontend/` — web UI (planned)
- `docker/` — Dockerfile, example compose stack, env template
- `docs/` — architecture and operations notes
- `.gitea/workflows/` — CI: build image, push to registry

## Status

Early scaffold. Interfaces are stable; provider export coverage expanding.
