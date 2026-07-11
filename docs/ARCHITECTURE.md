# IdPVault Architecture

## Components

- **API (FastAPI)** — tenant management, backup triggers, snapshot browsing, diffs, restore.
- **Scheduler (APScheduler)** — cron-per-tenant backup jobs inside the app process.
- **Provider adapters** — one module per IdP implementing `ProviderAdapter`
  (`validate_credentials`, `export`, `restore_object`). Adding a provider = adding one module.
- **Storage** — snapshots on disk: `data/<tenant>/<UTC timestamp>/objects.json.enc` + `manifest.json`.
  Encrypted with a per-tenant data key, wrapped by the master key (envelope encryption).
- **Postgres** — tenants, snapshot index, job history, audit log. Credentials for IdPs are
  stored encrypted; the master key lives in a file mount, never in the DB or env.

## Security model

- Master key: 32-byte file mounted read-only into the container (`secrets/master.key`).
- IdP API tokens: AES-256-GCM encrypted at rest with per-tenant data keys.
- Snapshots: encrypted at rest, same envelope scheme.
- Every mutating API action writes an audit row.
- UI/API auth: session login planned before any multi-user / SaaS use.

## Backup flow

scheduler fires -> adapter.export() pages through IdP API -> objects grouped by resource type
-> serialized, encrypted, written to snapshot dir -> manifest + DB row -> diff vs previous
snapshot -> drift alert webhook if changes detected.
