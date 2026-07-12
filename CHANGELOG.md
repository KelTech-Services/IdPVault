# Changelog

All notable changes to IdPVault are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions are the deployed image tags.

## [0.7.12] — 2026-07-12
### Changed
- Restore dialog: removed the redundant "Everything in this snapshot" radio;
  per-item checkboxes with explicit **Select all / Unselect all** buttons are the
  selection mechanism.
- Non-auto-restorable resource types (e.g. Okta apps) are now **visible** in the
  restore preview whenever they differ from live — a deleted app shows as
  `create … unsupported` with an explanatory note — instead of being silently
  omitted from the plan. Unchanged unsupported objects stay hidden to avoid noise.
### Fixed
- Restore summary key typo (`statuss` → `statuses`) — the Applied line in the UI
  and restore alerts now show the per-status counts (skipped/updated/failed/…).
- Header version badge was hardcoded (stuck at v0.7.2); it now reads the running
  version from `/healthz`.

## [0.7.11] — 2026-07-12
### Added
- Config restore-apply for **Okta** (`push_object`): create-or-update of groups,
  network zones, all four policy types, identity providers, and event/inline hooks
  via the Okta API (PUT full-replace to update, POST to create), through the
  adaptive rate limiter, with dry-run preview, per-object reporting, and the
  existing confirm gate. System objects (system policies/zones, BUILT_IN and
  app-sourced groups) are skipped; apps, authorization servers, schemas, and
  profile mappings are deliberately excluded from auto-restore (backed up and
  browsable, restore is roadmap).
### Changed
- Okta server-managed fields (`created`, `lastUpdated`, `lastMembershipUpdated`,
  `_links`, `_embedded`) no longer count as config in drift/restore comparison —
  plans and events show only real changes.

## [0.7.10] — 2026-07-11
### Fixed
- Drift detection silently broken for any tenant with identity backup enabled: the
  `identities/` sub-directory was being listed as a config snapshot and, sorting
  after the timestamped snapshots, made every backup diff the just-written snapshot
  against itself — so drift was never detected and the Events feed stayed empty.
  `list_snapshots` now only returns real config snapshots (dirs containing
  `objects.json.enc`), which also corrects retention pruning.

## [0.7.9] — 2026-07-11
### Fixed
- Auth0 restore self-heal now also handles **nested** read-only fields: Auth0 reports
  some rejected fields as "Additional properties not allowed: X on property Y" (e.g.
  `jwt_configuration.secret_encoded`); the write descends into the named sub-object,
  strips the field, and retries — not just top-level fields.

## [0.7.8] — 2026-07-11
### Fixed
- Auth0 config restore writes now self-heal against Auth0's strict schema: when a
  PATCH/POST is rejected with "Additional properties not allowed: <field>" (a
  read-only/computed export field), that field is dropped and the write retried,
  looping until Auth0 accepts the body. Only fields Auth0 explicitly rejects are
  dropped, so real config is never lost. `callback_url_template` is pre-stripped.

## [0.7.7] — 2026-07-11
### Fixed
- Restore/drift comparison no longer treats server-assigned identity and timestamp
  fields (`id`, `client_id`, `created_at`, `updated_at`) as configuration, so an
  object that differs only by its internal id (e.g. an Auth0 role deleted and
  recreated with a new id) correctly shows as identical instead of a phantom update.

## [0.7.6] — 2026-07-11
### Fixed
- Config restore now matches snapshot objects to live objects by **natural key**
  (name / identifier / client_id) instead of the server-assigned id, so an object
  that was deleted and recreated (with a new id) is recognized as existing — no more
  duplicate-create 409s — and updates PATCH the correct live object.
- **Auth0 rate limiting**: Management API calls (export and restore) retry on HTTP
  429 with backoff honouring `Retry-After`, so restores no longer fail when the
  tenant's global Management API limit is hit.
### Added
- Per-item selection in the restore preview: actionable rows (create/update) get
  checkboxes and a select-all, and Apply restores only the checked objects — the
  config-restore analog of the identity tool's per-user selection.

## [0.7.5] — 2026-07-11
### Added
- Config restore-apply for **Auth0** (`push_object`): create-or-update of clients,
  connections, resource servers, roles, and rules via the Management API (PATCH to
  update, POST to create), with dry-run preview, per-object reporting, and the
  existing confirm gate. Auth0 system objects and singletons (tenant settings,
  branding, custom domains, actions) are excluded from restore.
### Changed
- Restore ordering and never-restore sets moved onto each provider adapter
  (`restore_order` / `never_restore`) instead of being hardcoded to Authentik, so
  each IdP restores in its own dependency order. Authentik behavior unchanged.
- Object identity now recognizes Auth0 `client_id` (restore + diff matching) so
  clients no longer collide on an empty id.

## [0.7.4] — 2026-07-11
### Fixed
- Auth0 backup aborting on a single endpoint: single-fetch endpoints (tenant
  settings, custom domains, branding) are no longer sent pagination params
  (Auth0 returned 400 for `/custom-domains`), and feature-gated/deprecated
  endpoints (custom domains = paid, rules = deprecated) that return 4xx on a
  tenant are skipped rather than failing the whole export. Core endpoints stay
  strict.

## [0.7.3] — 2026-07-11
### Changed
- Auth0 adapter now authenticates via OAuth2 **client-credentials**: the stored
  credential is the M2M app's `client_id:client_secret`, and the adapter mints a
  fresh Management API token per run (cached until near expiry). Fixes scheduled
  Auth0 backups silently failing once a pasted Management token expired (~24h).
### Added
- Provider-driven Add-Tenant form: Provider is chosen first and the required fields
  adapt to it — Auth0 shows Client ID + Client Secret; the Full-DR Postgres URL and
  identity-backup fields appear only for providers that support them.

## [0.7.2] — 2026-07-11
### Changed
- Spacing under the Public URL help text in Settings.

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
