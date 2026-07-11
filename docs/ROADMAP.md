# IdPVault Roadmap

Goal: feature parity with commercial IdP backup/recovery products, self-hosted,
single-image deploy, encryption-first. Providers: Authentik, Okta, Auth0.

## v0.2 — multi-user app (next)

**Authentication & accounts**
- Real login page (session cookies, hashed passwords in Postgres) replacing HTTP Basic
- First-run admin setup flow; profile menu + logout in header
- Admin page: create/disable users; roles: `admin` (full control), `user` (read-only)
- Invite flow: new account -> one-time emailed setup link to set password

**Settings**
- SMTP config (host/port/TLS/credentials encrypted at rest, from-address, test-send)
- Alert webhook (ntfy / Slack-compatible)
- Default schedule / retention for new tenants
- Master key + app health status

## v0.3 — recovery & visibility core

**Restore engine** (the headline feature)
- Object-level and multi-object restore from any snapshot, dependency-aware ordering
- Dry-run preview (what would change) before apply
- Authentik first; Okta and Auth0 adapters after
- Restore report: what was restored, what was skipped, secrets needing re-entry

**Events & dashboard**
- Events page: per-snapshot change feed (add/update/delete per object) from the diff engine
- Dashboard cards: data coverage status, backup schedule + last run, unbacked changes
  (poll IdP event/audit APIs since last snapshot)
- Search + filters across snapshots/events (by object type, name, date)
- Tenant detail view: history, status, counts, drift badges, next run

## v0.4 — alerts & reach

- Drift/failure alerts delivered: webhook + SMTP (Teams/Slack-compatible)
- Applications browser (per-tenant object explorer)
- Tenant cloning (full/partial) and cross-tenant config promotion
- Audit log viewer; Prometheus metrics
- Authentik "full DR" mode: optional encrypted pg_dump alongside config snapshots

## Later / SaaS-track
- Terraform / blueprint export
- Public website, subscription billing, branded org migration
