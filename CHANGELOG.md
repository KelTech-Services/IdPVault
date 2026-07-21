# Changelog

All notable changes to IdPVault are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions are the deployed image tags.

## [1.2.9] - 2026-07-21
### Added
- Find in backups: search the change history for any object by name on the
  Backups and Users & Access pages. Results show the object's change timeline
  and, for deleted objects, the last snapshot it was present in with a
  Restore shortcut. The Activity page also gained an object search box.
- Restore justifications: applying a restore (config and Users & Access) now
  opens a confirm dialog with a justification field. The justification is
  recorded in restore history, shown in the report, and included in the
  Restores alert. A new System setting can REQUIRE a justification on every
  restore apply (off by default).
- Restore applies now re-verify your password (the same protection as
  deleting backups); wrong attempts are audit-logged.
- Trend charts everywhere backups live: the tenant Overview charts now cover
  BOTH backup types on one timeline (config and Users & Access as separate
  lines in the object and size charts), the Backups page gained the
  config-only chart row, and the Users & Access page gained four of its own:
  changes per backup, directory over time (users, memberships, assignments),
  backup size, and backup duration with API call counts per run.
- Unbacked Users & Access changes: tenants with Users & Access backup enabled
  get a second card on the tenant Overview counting users, memberships, and
  assignments changed since the latest Users & Access snapshot, with a
  Backup Users & Access now button when something is pending. The check runs
  on the Live State users cache cadence (default 60 minutes) to respect
  provider rate limits, and skips the provider entirely right after a
  Users & Access backup.
### Changed
- Alert subscriptions are now per channel AND grouped: for email and for the
  webhook separately, pick Config Backups (changes, failures, overdue
  watchdog), Users & Access Backups (changes, failures), Restores, and an
  optional "Successful backups too" (no-change success alerts, off by
  default, follows whichever backup types are checked). Existing installs
  keep their current behavior until the new settings are saved.
- Restore history is now per page: the Backups page shows config restores and
  previews only, and the Users & Access page has its own Restore history with
  Users & Access restores only.
- Renamed for clarity: the Overview and dashboard drift cards are now
  "Unbacked config changes", the Backups page chart is "Changes per Config
  backup", and the restore-history badge spells out
  "Users & Access restore".
- Refresh Users from provider now also refreshes the Unbacked Users & Access
  changes card, at no extra API cost.
- The dashboard Storage used stat and storage-by-tenant chart now count ALL
  backups: config snapshots, Full-DR database dumps, and Users & Access
  snapshots (previously config snapshots only). The backups-per-day chart
  now includes Users & Access runs.
### Fixed
- Deleting or pruning config snapshots removed the files but left their
  database rows, inflating the dashboard storage stat; pruned Users & Access
  snapshots likewise stayed listed even though they were no longer
  restorable. Delete and retention pruning now clean up the rows, and a boot
  reconcile removes rows orphaned by earlier versions.
- The overdue-backup watchdog alert had no Settings checkbox, so saving alert
  settings silently unsubscribed it. It is now part of the Config Backups
  group and re-enables on the next settings save.

## [1.2.8] - 2026-07-20
### Added
- Restore history on the Backups page: every restore preview and apply
  (config and Users & Access) has been recorded since v1.1 - now there is a
  viewer. Each row shows when, who, type, source snapshot, and a summary;
  View reopens the full per-object report exactly as it looked at run time.
  Org-scoped users only see runs for tenants in their org.
- Users & Access restore reports now record WHO was touched: the names of
  created and reverted users and of added memberships/assignments appear in
  the report and in restore history (runs from before this release show
  counts only - the names were not captured then).
### Changed
- Config restore reports in restore history show only what was actually
  touched; identical and skipped objects are summarized in a footer instead
  of listed row by row.
- Restore history lists actual restores by default; dry-run previews are
  still recorded and can be shown with "include previews".
- "Backup now" buttons that trigger a config backup are now labeled "Backup
  config now" so they are not mistaken for a Users & Access backup.

## [1.2.7] - 2026-07-20
### Added
- Users & Access drift detection: every Users & Access backup is compared to
  the previous snapshot, and user, membership, and assignment changes become
  events in the same per-tenant Activity feed as config changes (renames are
  detected by immutable server id and shown as changes, not remove+add).
- Users & Access compare: the changes summary on each Users & Access snapshot
  is now clickable and opens a full compare vs the previous snapshot - users
  added, removed, and changed with per-field values, plus membership and
  assignment changes with readable names.
- Users & Access alerts: two new alert subscriptions in Settings, "Users &
  Access changes detected" (on by default, change list included in the
  message, same format as config backup alerts) and "Users & Access backup
  succeeded" (off by default). Both go to the webhook and admin email like
  every other alert.

## [1.2.6] - 2026-07-20
### Added
- Users & Access restore can now revert profile changes on existing users. The
  dry-run preview lists users whose profile fields differ from the snapshot
  (with the changed fields), each individually selectable and UNCHECKED by
  default - reverting a live user is always an explicit opt-in. Reverts never
  touch credentials, MFA, or lifecycle status, and Auth0 email addresses are
  deliberately excluded (email changes trigger verification side effects).
  Restore reports show a "reverted" count.
- Renamed users are recognized by their immutable server id (Authentik pk,
  Okta id, Auth0 user_id): a username change appears as a revertable change
  instead of a recreate, and apply never creates a duplicate of a renamed
  user.

## [1.2.5] - 2026-07-20
### Added
- Background job queue: manual backups, Users & Access backups, and Users &
  Access restores now run as background jobs instead of holding the request
  open. Long operations survive closed browser tabs, and the response is
  immediate.
- Activity area in the sidebar: shows every queued and running backup or
  restore (including scheduled nightly backups) with live progress, and
  briefly shows completions and failures. Org-scoped users only see jobs for
  tenants in their org.
- Live progress on the Users & Access page and restore dialog (percent when a
  prior run gives an expected total, live API-call count otherwise).
- Jobs interrupted by an app restart are marked failed on boot instead of
  appearing to run forever.

## [1.2.4] - 2026-07-20
### Added
- Database schema management now uses Alembic migrations. Upgrades are applied
  automatically at boot; existing installs are adopted in place on first boot
  after updating, and fresh installs are unaffected. No operator action needed.
### Changed
- License page: the hard-refresh note now only suggests a refresh if paid
  features do not appear after installing a key (the app already refreshes
  itself in most cases).

## [1.2.3] - 2026-07-19
### Security
- Snapshot read, delete, and cache-file paths are now built and containment-checked
  inline at every filesystem access (normalized path + prefix check on the data
  directory), verified locally with CodeQL to produce zero path-injection findings.
  Replaces the 1.2.2 helper approach. No behavior change.

## [1.2.2] - 2026-07-19
### Security
- Hardened snapshot file access: manifest and changes-cache reads/writes now go
  through a containment-checked path helper, resolving CodeQL path-injection
  findings. No behavior change.

## [1.2.1] - 2026-07-19
### Fixed
- CRITICAL (Authentik): the applications list API filters results through the
  access-policy engine, so applications protected by a policy binding were
  silently MISSING from backups and the Live State view. All application
  fetches now request the full superuser list. The API token's user must be a
  superuser for complete backups - the docs now say so. If your backups
  looked smaller than your tenant, run a fresh backup after upgrading.
- Restore report honesty: a 400 naming a reference field (target, policy,
  group, ...) is no longer retried with that field dropped, which was turning
  real errors into "field is required" noise. Bindings whose target no longer
  exists anywhere are excluded from the plan as calm skips with a clear
  explanation instead of being attempted and failed.
### Added
- Brand icons: the Simple Icons set (pinned, bundled into the image at build
  time, so the app still makes zero external calls) shows product logos with
  brand colors in Live State application rows and global search results, with
  a colored initials fallback; the authentik/okta/auth0 tags carry their
  logos.
- Instant tooltips: informational tooltips appear immediately instead of
  after the browser's long hover delay.
- Unbacked changes updates the moment a backup completes (drift is zero by
  definition right after a backup) instead of waiting for the next live-state
  poll; Backup now also refreshes the Overview in place.

## [1.2.0] - 2026-07-18
### Added
- Tenant Overview redesigned as a Live State workspace: full-width
  master-detail layout with a category rail (Directory / Applications /
  Security & access / System) showing live object counts and drift chips vs
  the latest backup, an object table with a backup-status pill per object,
  and a side-by-side detail view with changed fields called out. Per-object
  Restore from both the rows and the detail view.
- Live users (Business/MSP): the Directory rail includes the live user
  directory compared against the latest Users & Access snapshot. Loaded on
  demand and cached (new "Live State users cache" setting, default 60
  minutes) so user APIs are never hammered; Restore on a deleted user opens
  the Users & Access restore with that user preselected. New "Refresh Config
  from provider" and "Refresh Users from provider" buttons, both debounced.
- Global Live State search: one box searches every category at once by name
  or id (users included once the directory is loaded), with grouped results
  that click through to object detail.
- Changes page: compare any backup against any other backup or the current
  live configuration - totals, per-category filter chips, changed-field
  lists, before/after JSON, and Restore from the From backup. The Unbacked
  changes stat on the Overview clicks through to it.
- Tenant trend charts (changes per backup, objects over time, backup size)
  with a Show/Hide Trends toggle.
- Backups page: Type (manual/auto) and Status columns; failed backup
  attempts now appear in the list with their error instead of disappearing.
- Users & Access page: changes-vs-previous summary per snapshot.
- Server-side paging on Live State object and user lists with a 50/100/250
  page-size picker; lists are name-sorted and counts always reflect the full
  set.
### Changed
- Full-width app shell (the centered content cap is gone) plus a typography
  pass: larger section titles and stat values.
- The Overview's status cards and out-of-sync banner merged into a single
  status strip (health, schedule, last backup, unbacked changes, Backup now).
- The Explorer page merged into the Overview's Live State panel; snapshot
  browsing lives on the Backups page. Old Explorer links land on Overview.
- Policy and flow-stage bindings show readable labels (what they bind)
  instead of raw UUIDs in Compare and Changes.
- Restore buttons are always visible on object rows, and every informational
  tooltip carries the visible info icon.
### Fixed
- Failed backup attempts are no longer invisible on the Backups page.

## [1.1.6] - 2026-07-18
### Added
- Snapshot Explorer (tenant sidebar): open any snapshot and browse it by
  category, with a status badge on every object vs the latest backup
  (unchanged / modified / deleted in latest / new in latest), side-by-side
  object detail with the changed fields called out, and a one-click object
  Restore that opens the dry-run preview with just that object selected.
- Backups page: the snapshot table now shows objects captured, snapshot size,
  Full-DR dump size, and a changes-vs-previous summary per snapshot (computed
  once and cached). Both backup tables support multi-select with select-all.
- Backup deletion: admins can bulk-delete config and Users & Access snapshots.
  Deletion asks for your password (re-auth), and both deletions and denied
  attempts are recorded in the audit log.
### Changed
- "Diff selected" is now "Compare", and results render as a readable table
  (change, category, object, changed fields) with per-object JSON on demand
  instead of a raw JSON dump.
- Builds stamp asset URLs with the commit id, so browsers and CDNs can never
  serve a stale script or stylesheet after an upgrade.
- Leftover Close buttons removed from the Backups and Users & Access pages;
  Delete buttons sit to the right of Compare / Back up now.

## [1.1.5] - 2026-07-18
### Security
- The snapshot browse and object detail endpoints now enforce tenant scoping
  like every other tenant route. Before this fix, any authenticated user could
  read any tenant's snapshot contents by id; on MSP installs that let
  org-scoped users read other client organizations' backed-up configuration.
  Single-organization installs are unaffected in practice (non-admin roles are
  read-all by design there). A regression test now guards the scoping check.
### Added
- Full-DR: an "event and session data" option per tenant. Excluded skips the
  rows of ephemeral and history tables (login sessions, background task logs,
  event history) from the database dump - typically a 90%+ size reduction on
  long-running instances. Tables stay in the dump, so restores boot clean.
  Default is Included (existing behavior).
- Authentik Users & Access snapshots now capture and count policy bindings
  that reference a group or user. The snapshot table shows "Policy bindings"
  for Authentik tenants instead of a structurally-zero "Assignments" column
  (Authentik grants app access via bindings, not direct assignments).
### Changed
- Tooltips are now carried by a visible (i) icon next to the label or control
  instead of invisible hover-on-text, everywhere in the app.
- The Full-DR field explains dump size behavior; docs updated to match.

## [1.1.4] - 2026-07-18
### Changed
- New tenant-scoped navigation (v1.2 shell, phase 1). The tenant becomes the
  workspace: picking a tenant (dropdown, top of the sidebar) opens its own
  pages - Overview, Backups, Users & Access, Activity, and Settings. With a
  single tenant the app enters that workspace directly and shows no dropdown.
  With two or more, the all-tenants dashboard is the landing page, reachable
  any time via "All tenants".
- Admin pages (Orgs, App users, Audit, License, System settings) moved into a
  collapsible Administration group. License and System settings are now
  separate pages. The app-users page is titled "App users" to distinguish it
  from IdP users. Items an installed license does not include are hidden
  rather than shown locked.
- The global Events page is replaced by a per-tenant Activity page.
- Pages are deep-linkable: tenant pages at #/t/<id>/<page>, global pages at
  #/<page>. Old links redirect.
- Timestamps in the UI are shown in the browser's local time and honor the
  profile time format; the "(UTC)" table columns are gone. Snapshot names and
  storage remain UTC on the backend.
- Backup schedules display as friendly labels ("Daily - 12:00 AM") instead of
  raw cron; custom cron schedules still display as entered.
- The frontend is split into static css/js modules (no build step, same
  image). Assets carry versioned URLs so browsers pick up new code on update.
### Added
- Installing a license now applies its features immediately - the app
  re-reads the session and updates the nav without a sign-out. A note under
  the license key field recommends a hard refresh after install.

## [1.1.3] - 2026-07-17
### Fixed
- Master key read no longer strips an exact 32-byte key. Key material is
  random bytes, so about 1 in 22 fresh installs generated a key starting or
  ending with a whitespace byte that the reader then stripped, and the app
  rejected its own key ("must be exactly 32 bytes") on every encrypt. Keys in
  files longer than 32 bytes (hand-made, trailing newline) are still trimmed.
  Existing working installs are unaffected: a key that read correctly before
  reads identically now.
- First-boot umask leak: generating the master key set a restrictive umask that
  leaked into the app process, so every directory the app created before the
  first container restart was unwritable by its own owner (0500) and first
  backups failed with a permission error. The umask is now scoped to the key
  write only, and the entrypoint self-heals any dirs or files left
  owner-unwritable by earlier versions - updating the image fixes affected
  installs automatically.
- Community tier: the tenant form no longer offers Users & Access backup when
  the installed license lacks it. The control is disabled with a short note
  instead of failing at save time with an error. (Applies on every path into
  the form, including the first "+ Add tenant" open.)
- The UI shell (HTML) is served with Cache-Control: no-cache, so browsers pick
  up a new version immediately after an image update instead of running stale
  JS against a newer API.
### Added
- `PUID` / `PGID` environment variables: run the app under your own user/group
  ids (defaults stay 10001/10001). Removes bind-mount ownership friction on
  NAS setups and matches the common self-hosted convention.
### Changed
- Alert titles, API error messages, and email text use "-" instead of an
  em-dash, matching the project style rule for user-facing text.

## [1.1.2] - 2026-07-13
### Changed
- Dates are US-format (MM/DD/YYYY) everywhere users see them: org renewal
  dates in the Orgs table and dashboard renewals card, the CSV template
  example, and CSV export. The org CSV import accepts MM/DD/YYYY (and the
  previous YYYY-MM-DD for compatibility); storage stays ISO internally.

## [1.1.1] - 2026-07-13
### Security
- Org CSV import: row-level error messages for malformed input no longer
  include raw exception text (which could expose internal details). Our own
  validation messages (bad cadence, bad date, missing name) are unchanged.
  Resolves the CodeQL "information exposure through an exception" finding.

## [1.1.0] - 2026-07-13
### Added
- Org bulk CSV tools on the Orgs page (MSP): Export CSV (all orgs, all
  fields), Template (correct columns plus an example row), and Import CSV.
  Import validates each row like the form, skips rows whose org name
  already exists (never overwrites), and reports imported / skipped /
  error counts with row numbers.
- Multi-architecture image: builds now publish linux/amd64 and linux/arm64
  under the same tag. Apple Silicon Macs, Raspberry Pi, and other ARM hosts
  pull a native image instead of running under emulation. The compose stack
  is unchanged and works on Linux, macOS, and Windows (Docker Desktop/WSL2).
- README: platform support section covering Linux/NAS, macOS, Windows, and
  ARM boards, with named-volume and reverse-proxy guidance.

## [1.0.0] - 2026-07-13

IdPVault 1.0. The full product surface is shipped and validated in production:

- Config backup, drift detection, and restore (with dry-run) for Authentik,
  Okta, and Auth0.
- Users & Access backup and restore (users, group memberships, app
  assignments) for all three providers.
- Client orgs and org-scoped roles for MSPs (v0.9.0), validated live:
  org-scoped users see only their own org's tenants, org admins can back up
  and restore them, org viewers are read-only.
- Envelope encryption at rest, offline Ed25519 license verification, no
  telemetry, no phone-home.
- Zero-config deployment: `docker compose up` with no required configuration,
  self-seeding master key with a never-regenerate guard.
- VChart dashboard, light/dark themes, timezone-aware scheduling, serial
  backup queue, alerts (email/webhook), audit log, TOTP MFA.

No functional changes from 0.9.0; this release marks the version milestone.

## [0.9.0] - 2026-07-13
### Added
- MSP tier (requires a license with the `msp` feature):
  - Client orgs: group tenants per client, with contact info, notes, billing
    memo, billing cadence, and next renewal date. New Orgs page (admin only).
  - Org-scoped roles: `org_admin` (backup, browse, restore within their org's
    tenants) and `org_viewer` (read-only within their org). One org per
    scoped user.
  - Server-side scoping on every endpoint: tenants, snapshots, diffs, backups,
    restores, Users & Access, dashboard summary/trends, events, and recent
    runs are all filtered by the user's org. Tenants outside the org return
    404 so other clients are never enumerable.
  - Dashboard renewals card: orgs with a renewal date in the next 60 days
    (or overdue) surface for admins.
  - New doc page "MSP & client orgs", shown only when an MSP license is
    installed.
- `/auth/me` now returns the license feature list so the UI can gate
  features for non-admin users without exposing license details.
### Changed
- Deleting an org keeps its tenants and users (they are unassigned, not
  removed). Roles admin/user continue to behave exactly as before; existing
  installs are unaffected until an MSP license is installed.

## [0.8.15] - 2026-07-13
### Security
- Follow-up to 0.8.14: snapshot paths now also pass a realpath containment
  check (resolved path must stay inside the data directory), and the webhook
  test response is built exclusively from untainted values. Defense in depth,
  and shaped so static analysis can verify it.

## [0.8.14] - 2026-07-13
### Security
Hardening pass resolving all findings from the first public CodeQL scan:
- Path-safety: tenant slugs and snapshot timestamps are strictly validated at
  the storage boundary (and slug format at tenant creation), making path
  traversal structurally impossible even for authenticated admins.
- Exception details no longer flow into HTTP responses (webhook/email test
  errors, pg_dump failure text) - full detail goes to server logs instead.
- CI workflow GITHUB_TOKEN now defaults to read-only permissions; jobs that
  need more declare it explicitly.

## [0.8.13] - 2026-07-12
### Added
- **Dashboard charts** (VisActor VChart, vendored - no CDN): changes over the
  last 14 days (stacked add/update/delete), backup runs by outcome, and storage
  by tenant (donut). Theme-aware - charts re-render when you flip light/dark.
  New `/dashboard/trends` endpoint feeds them.
- **Design pass**: Inter typeface (vendored, self-hosted), tabular numerals in
  tables and stat cards, brand palette pulled from the logo (accent is now the
  logo blue; gold appears as the active-nav indicator and paid-tier name),
  refined radii (larger on cards/modals), button transitions, visible focus
  rings for keyboard navigation.
### Fixed
- Users & Access restore preview no longer prints a user's email twice when
  the display name is the email.

## [0.8.12] - 2026-07-12
### Added
- **Auth0 Users & Access backup + restore** - the last provider gap. Backup
  captures users (paged, with a loud failure instead of a silently partial
  export past Auth0's 1,000-record pagination cap), role assignments, and
  organization memberships (roles and orgs ride the group bucket with a `kind`
  tag; Auth0 has no user-to-app assignment concept). Restore is create-only and
  additive like the other providers: missing users are recreated blocked with a
  random password (send a reset, then unblock), matched by email; role/org
  edges re-added by natural key. Social/enterprise users are reported, not
  recreated (their identity lives at the external IdP). Organizations are
  skipped gracefully on plans without the feature.
- Tenant form now offers Users & Access for Auth0; provider-specific manual
  steps shown in restore previews. 3 new mapping tests (25 total).

## [0.8.11] - 2026-07-12
### Changed
- Tenant form placeholders are now provider-aware neutral examples (Acme Okta /
  acme-okta / https://acme.okta.com etc.) instead of KelTech-specific text.
- Logos are sized by width in both themes, so the dark-theme logo now renders
  at the same visual size as the light-theme one (sidebar and login card).

## [0.8.10] - 2026-07-12
### Changed
- Theme toggle moved to its own icon button beside the username (right of
  "user · role" in the sidebar), leaving Profile and Log out as a clean pair.

## [0.8.9] - 2026-07-12
### Added
- Light/dark theme toggle (sun/moon button in the sidebar user area). Preference
  is saved per user in their profile, with a local fallback so the login screen
  remembers it. Logos swap automatically per theme; the entire palette is
  CSS-variable driven, dark remains the default.
### Fixed
- Links now use the accent color (were browser-default blue, unreadable on the
  dark background).
- Tenant form: "Users & Access backup" label no longer wraps to two lines and
  misaligns the grid (detail moved to a hover tooltip).

## [0.8.8] - 2026-07-12
### Added
- Zero-config deployment: `docker compose up -d` is the entire install. A new
  entrypoint generates the master encryption key on a truly fresh first boot,
  fixes volume ownership, then drops root -> uid 10001. Compose example (and
  README) rewritten around named volumes with zero prerequisites; bind-mount
  swap documented.
- Never-regenerate protection, two layers: the container refuses to start if
  the key file is missing while data exists, and the app refuses to boot if
  the mounted key cannot decrypt the existing database. No path silently
  regenerates a key over encrypted data.
- Setup wizard and deployment doc now tell you to back the key up immediately,
  with the exact `docker cp` command.
### Changed
- README refreshed: copy-pasteable compose stack, updated status/layout, serial
  queue + org-timezone noted in features.

## [0.8.7] - 2026-07-12
### Added
- Friendly scheduling: Daily/Weekly/Monthly + time dropdowns (with a custom-cron
  escape hatch) replace raw cron fields in the tenant form and Settings; new
  org-wide default Users & Access schedule; new tenants prefill from org defaults.
- Org timezone (Settings): all schedules are evaluated in it, DST-aware; jobs
  re-register automatically when it changes. Snapshot names/storage stay UTC.
- Per-user time format (Profile): automatic / 12-hour / 24-hour; Events and
  Audit timestamps render in the viewer's local time.
- Serial backup queue: backups run one at a time by default (safe for modest
  hosts), each starting the moment the previous finishes; opt into parallelism
  with IDPVAULT_BACKUP_WORKERS. A 1-hour misfire grace runs delayed jobs late
  instead of skipping them.
- Licensing doc: three-plan comparison (Community / Business / MSP) with the
  idpvault.com purchase link; "Get a license" link in Settings -> License.
- Docs topics support license-feature gating (groundwork for MSP-only pages).
### Changed
- The identity feature is now presented as "Users & Access" across the UI and
  docs ("identity" is overloaded in IAM). The license feature key stays
  `identity`, so existing keys are unaffected.

## [0.8.6] - 2026-07-12
### Added
- Clickable "Unbacked changes" dashboard card: opens a per-tenant breakdown
  (count, provider, last backup incl. failed status) with a Backup now button
  per row.
- Provider capability flag (`supports_identity`): the Identity button is
  disabled with an explanatory tooltip for providers without identity backup
  (currently Auth0), and the API rejects unsupported identity backups cleanly
  instead of recording a failed run.
### Changed
- Authentik identity-restore manual steps are now full-DR-aware: with a
  Postgres URL configured they point to the pg_dump for credential recovery;
  without one they explain credentials won't come back via API restore and how
  to enable full-DR.
- Alert subscription renamed: "Configuration drift detected" is now "Changes
  detected during backup" (the change list is included in the backup message).

## [0.8.5] - 2026-07-12
### Added
- "Set password now" option when creating users: the admin sets an initial
  password and the account is active immediately - no SMTP required. The invite
  link flow remains the default.
### Changed
- Identity restore UI rebuilt to match the config restore experience: same
  modal, dry-run preview into a plan table (action / object / status) with
  per-user checkboxes, Select all / Unselect all, and an apply button showing
  the selection count. Post-apply report uses the same table with per-category
  ok/failed statuses. Docs updated (users & security).

## [0.8.4] - 2026-07-12
### Changed
- Docs and UI copy style pass: em-dashes removed from all user-facing text
  (9 in-app doc pages, headings, tooltips, placeholders, toasts, empty-cell
  markers); licensing table "not included" cell now says so explicitly.
- Events table column header now reads "Snapshot (UTC)".

## [0.8.3] — 2026-07-12
### Added
- Smoke-test suite (22 tests: crypto envelope + tamper rejection, license
  verification/forgery/grace/gating, restore plan matching incl. rename vs
  recreate and binding remapping, password hashing, alert formatting) and CI
  now **runs the tests before building** — a failing test blocks the image
  from ever reaching the registry.

## [0.8.2] — 2026-07-12
### Added
- **In-app Docs.** New "Docs" item in the sidebar (visible to all users) with
  nine guides: getting started, backups & snapshots, drift & events, config
  restore (per-provider coverage), identity backup & restore, alerts, users &
  security, licensing & tiers, and deployment & proxy.

## [0.8.1] — 2026-07-12
### Added
- **User seats are license-gated.** The free Community tier includes exactly one
  account — the admin created at first run. Adding users requires a paid license
  (which is unlimited-seats unless the key carries a `max_users` cap; mint tool
  gains `--max-users`). Enforced server-side (402) and in the UI (Add user
  grayed with an explanatory tooltip); the License panel shows the seat count.

## [0.8.0] — 2026-07-12
### Added
- **Open-core licensing.** IdPVault runs free in the Community tier (1 tenant,
  full config backup & restore) and unlocks more tenants plus identity backup &
  restore with a paid license key. Keys are Ed25519-signed tokens verified
  entirely OFFLINE against a public key embedded in the app — no phone-home,
  no telemetry, nothing leaves your network.
- Settings → License: install/remove a key, see tier, customer, expiry date with
  countdown, entitlements, and grace-window warnings. Renewal keys can be
  installed early — their term extends from the previous expiry.
- Server-side enforcement at every paid door (not just hidden buttons): tenant
  creation beyond the licensed limit, backup/restore for over-limit tenants, and
  all identity backup/restore paths return 402 with a clear message; scheduled
  jobs for unentitled tenants skip with a log line. UI grays the affected
  buttons with explanatory tooltips.
- **Non-destructive downgrade.** If a license expires (after a 3-day grace
  window) or is removed, nothing is deleted: the oldest tenant stays fully
  live, other tenants keep their data and snapshots but pause backup/restore,
  and identity features pause — everything resumes the moment a valid key is
  installed.
- `tools/mint_license.py` — KelTech-internal key minting (Ed25519, annual/multi-
  year, `--extend-from` for renewals).
- Repository licensed under the **Business Source License 1.1** (production use
  permitted except offering IdPVault as a hosted service; converts to Apache
  2.0 on 2030-07-12).

## [0.7.22] — 2026-07-12
### Changed
- Bindings in alerts and events are labeled by what they connect
  ("binding: app-it-tools-user") instead of "?".
- Alert change lines use `[+] [-] [~]` prefixes — leading `+`/`-` characters are
  markdown bullets in Slack/Mattermost and were being swallowed by the renderer.

## [0.7.21] — 2026-07-12
### Changed
- **One email per backup run.** Drift is only ever detected during a backup, so
  the change details now ride inside the backup email ("Backup complete — changes
  detected", listing what was added/removed/changed per object) instead of a
  second back-to-back drift email. Subscription categories unchanged: with
  changes it sends under "drift detected" (falling back to "backup succeeded" if
  that's what's subscribed); without changes, the plain backup email.

## [0.7.20] — 2026-07-12
### Fixed
- Authentik scalar list fields (e.g. an outpost's `providers`, `property_mappings`)
  are compared as SETS — Authentik returns them in arbitrary order, which made the
  Embedded Outpost show a permanent phantom "update" (same 15 providers, shuffled).

## [0.7.19] — 2026-07-12
### Fixed
- Restore comparison is now **remap-aware**: snapshot objects have their internal
  cross-references translated to current live ids before diffing (Authentik), so a
  binding referencing a recreated app compares as identical instead of showing a
  perpetual phantom "update" on every restore run. Restores now fully converge.

## [0.7.18] — 2026-07-12
### Fixed
- Authentik policy/flow-stage **bindings no longer duplicate on re-restore**:
  bindings have no name, so they now match by a composite of what they connect
  (policy/group/user + target + order, with id remapping applied) instead of
  their server-assigned pk — a binding recreated in a previous run is recognized
  as the same binding. Note: restore is additive; a duplicate created before this
  fix must be removed manually in Authentik.

## [0.7.17] — 2026-07-12
### Fixed
- Authentik `pk` no longer counts as config in comparisons — a recreated object
  whose only difference is its new internal id shows `identical` instead of a
  phantom update.
- Authentik updates for applications and flows now use the correct SLUG detail
  routes (their APIs aren't keyed by pk) — fixes an update wrongly falling back
  to create and failing on slug uniqueness.

## [0.7.16] — 2026-07-12
### Fixed
- **Duplicate-create trap on re-restore**: plan matching is now hybrid — internal
  id first (renamed objects still match themselves), then natural key
  (slug/name/label). Previously an object that had already been recreated (new
  internal id) showed as "create" again on the next preview of an older snapshot,
  and applying could create a duplicate (Okta allows duplicate app instances).
  Per-provider natural keys added for Authentik (slug/name/domain) and Okta
  (app label, group name, policy/zone/idp/hook name).

## [0.7.15] — 2026-07-12
### Fixed
- **Authentik reference remapping**: objects that reference other objects by
  internal id (policy bindings → application, app → provider, flow references)
  now resolve deleted-and-recreated targets by natural key (slug/name) — old
  snapshot ids are remapped to the current live ids, including recreations from
  a previous restore run. Fixes `POST policies/bindings -> 400 {"target": …}`
  after restoring a deleted application.
### Changed
- Okta's server-derived per-app objects (app user schemas, profile mappings) no
  longer appear in restore plans at all — Okta regenerates them automatically
  when an app is recreated, so they were noise/scary-red rows with no action.
  They remain backed up and browsable in snapshots.
- Object names in plans/events prefer the friendly `label` over the internal
  `name` (Okta apps now show "GoDaddy", not `godaddy`).

## [0.7.14] — 2026-07-12
### Fixed
- **Authentik application restore was broken** ("no write path known for None"):
  objects from non-polymorphic endpoints (applications, policy bindings, groups,
  flows, brands, certificates, outposts) carry no `meta_model_name`, so write-path
  resolution failed. A resource-type fallback map now routes them correctly —
  deleting an Authentik app and restoring it works.
- Authentik updates switched from PUT to **PATCH** with self-heal (drop the exact
  fields a 400 error names, retry): a full-replace PUT re-validated untouched
  fields (e.g. a proxy provider's `external_host`) and failed updates that never
  meant to change them.
- Phantom provider updates eliminated: Authentik's denormalized read-only
  back-references (`assigned_application_name/slug`, backchannel variants) no
  longer count as config in drift/restore comparison — deleting an app no longer
  flags its provider as "changed".

## [0.7.13] — 2026-07-12
### Added
- **Okta app restore-apply.** Deleted apps are recreated from the snapshot
  (label, sign-on mode, settings, visibility, profile; original active/inactive
  state honored) and changed apps are updated in place. Because IdPs redact
  secret material from every export, a recreated app comes back with
  **regenerated credentials** (new OIDC client secret / SAML cert) — the restore
  report marks these `created_new_credentials` so you know to re-point the
  integration. Okta's own internal apps are skipped. Group↔app assignments are
  re-linked via identity restore (natural-key remapping).
### Changed
- Shortened the not-auto-restorable note so it isn't truncated in the report.

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
