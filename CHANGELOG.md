# Changelog

All notable changes to IdPVault are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions are the deployed image tags.

## [0.7.1] — 2026-07-11
### Added
- Deployment / reverse-proxy hardening: canonical **Public URL** setting used for
  email links, HTTPS/secure-cookie detection (honours `X-Forwarded-Proto`), and an
  optional strict **Host enforcement** toggle (default off; health/metrics exempt).
- uvicorn started with `--proxy-headers` for correct scheme/host behind a proxy.

## [0.7.0] — 2026-07-11
### Added
- Login brute-force protection: configurable failed-attempt lockout (default 5
  attempts, 15-minute lockout), tracked per account; audit-logged on lockout.
- Security response headers (X-Content-Type-Options, X-Frame-Options: DENY,
  Referrer-Policy, Content-Security-Policy).
- Session/trust cookies set `Secure` automatically when served over HTTPS.
- Self-health alerting: a daily check alerts (new "Backup overdue / stale"
  category) if a scheduled tenant has no recent successful backup.
- Settings for login policy (max attempts, lockout minutes) and stale-backup
  threshold (hours).

## [0.6.11] — 2026-07-11
### Added
- Design pass 2: loading skeletons (shimmer rows) replace "Loading…" text.
- Empty states with icon + message + call-to-action across all tables.
### Changed
- Tighter in-table action-button density.

## [0.6.10] — 2026-07-11
### Changed
- Emails are now branded HTML (inline logo via CID, rounded card, structured
  fields) with a plain-text fallback. Applies to all types: alerts, invites,
  password resets, test email.

## [0.6.9] — 2026-07-11
### Added
- Rich alert formatting: Slack/Mattermost/Discord attachments with a colored
  severity bar, title, body, and structured fields.
- Alert event catalog with per-event subscriptions: configuration drift,
  backup failed, backup succeeded (off by default), restore applied.
- backup-success and restore-applied alerts wired into the pipeline.

## [0.6.8] — 2026-07-11
### Fixed
- Webhook payload format for Mattermost and generic incoming webhooks (now sent
  Slack-compatible instead of ntfy-style).
### Added
- Webhook format selector (auto / Slack / ntfy), "Send test alert" button, and
  in-app explanation of what alerts fire and when.

## [0.6.7] — 2026-07-11
### Changed
- Settings reorganized: core security/operations settings on top, optional SMTP
  and Alerts below.

## [0.6.6] — 2026-07-11
### Added
- Admin "Reset MFA" for a user (e.g. lost authenticator).
- Configurable MFA trusted-device lifetime — skip the code within a set window;
  default 0 (always prompt).

## [0.6.4 – 0.6.5] — 2026-07-11
### Added
- Design pass 1: left sidebar navigation, refined design-token system, inline
  SVG icons, sticky topbar with page title.
### Fixed
- Tenant and user action buttons kept on a single row.

## [0.6.3] — 2026-07-11
### Added
- Hash routing — refresh, back/forward, and bookmarks preserve the current page.

## [0.6.1 – 0.6.2] — 2026-07-11
### Added
- Admin-triggered password reset; self-service profile email; "Forgot password"
  on the login screen (emailed reset link).
### Changed
- Tenant/user Delete moved to a red button; row actions consolidated.

## [0.6.0] — 2026-07-11
### Added
- Zero-config first-run setup wizard (create admin in-browser; no admin creds in
  the stack YAML required — env-var bootstrap still supported for headless).
- Self-service profile: change password, enable/disable TOTP MFA (stdlib TOTP +
  QR enrollment; secrets encrypted at rest); MFA enforced at sign-in.

## [0.5.0 – 0.5.6] — 2026-07-11
### Added
- Identity backup (opt-in per tenant): users, group memberships, and
  provenance-aware app assignments (group-inherited vs direct), separate
  schedule/storage/retention.
- Header-driven adaptive Okta rate limiting with configurable reserve headroom;
  measured backup-duration estimate and cadence recommendation.
- Identity restore: dry-run preview, then create-only additive apply with
  natural-key ID remapping (login / group name / app label) so recreated-object
  id changes don't break edges. Selectable per-user restore.
- Okta config coverage completed: profile mappings, user types, per-app schemas.
### Changed
- Volatile fields (usage counters, denormalized expansions) normalized out of
  drift detection and restore planning.

## [0.4.0 – 0.4.2] — 2026-07-11
### Added
- Drift + backup-failure alerts (webhook + email).
- Audit log viewer, snapshot object browser, Prometheus metrics endpoint.
- Tenant clone/promote (restore a snapshot into another same-provider tenant).
- Authentik full-DR mode (encrypted pg_dump alongside config snapshots).
### Fixed
- Additive column-migration guard for post-table schema additions.

## [0.3.0] — 2026-07-11
### Added
- Restore engine: dry-run preview + dependency-ordered apply for Authentik with
  per-object restore reports.
- Events feed from backup diffs; dashboard cards (coverage, unbacked changes,
  storage); backup-run tracking including failures.

## [0.2.0] — 2026-07-10
### Added
- Multi-user: session login, admin/user roles (server-enforced), email invites,
  SMTP + settings, tenant editing with token rotation.

## [0.1.0] — 2026-07-10
### Added
- Initial release: encrypted config snapshots (envelope AES-256-GCM), provider
  adapters (Authentik / Okta / Auth0), cron scheduler, retention, diff engine,
  minimal web UI. Deployed as a single self-contained image + Postgres.
