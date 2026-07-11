# IdPVault — User Guide

Self-hosted backup, drift detection, and restore for Authentik, Okta, and Auth0.
This guide covers setup, what gets backed up, how restore works, and — importantly —
**what is automatic vs. what you must do manually after a restore.**

---

## 1. Concepts

IdPVault backs up two distinct layers, on separate schedules:

- **Configuration** (always on): apps, providers/OIDC/SAML settings, flows, stages,
  policies, groups, property/profile mappings, certificates, brands. This is the
  structure of your tenant. Small, fast, safe to back up frequently.
- **Identities** (opt-in, per tenant): users, group memberships, and app assignments.
  Larger, slower, rate-limit sensitive. Backed up on its own schedule.

Everything is encrypted at rest with envelope encryption (AES-256-GCM, per-tenant data
keys wrapped by a master key that lives only on your host).

---

## 2. Setup

1. Deploy the stack (see `docker/compose.example.yaml`). Generate the master key once:
   `head -c 32 /dev/urandom > secrets/master.key && chmod 400 secrets/master.key`.
2. **First-run wizard:** open the app — on a fresh install it prompts you to create the admin
   account (username + password) right in the browser. No admin credentials in the YAML needed.
   (Optional: set `IDPVAULT_ADMIN_USER` + `IDPVAULT_ADMIN_PASSWORD` for automated/headless
   provisioning instead; if both are set the admin is created from them and the wizard is skipped.)
3. **Add a tenant**: name, slug, provider, base URL, API token.
   - **Okta token**: Admin → Security → API → Tokens. Create it while signed in as a
     *read-only admin* for least privilege (backup needs read; restore needs write).
   - **Authentik token**: a service account or admin token with read (and write, for restore).
4. Optionally set a config backup schedule (cron, UTC) and retention.
5. Optionally enable **Identity backup** with its own schedule + retention (see §5).

---

## 3. What a CONFIG backup contains — and doesn't

Captured: the full config surface per provider (see §1). **Not captured, by design:**
secrets. IdP APIs never return OAuth client secrets, certificate private keys, or signing
keys. A restored object may come back needing its secret re-entered. This is true of every
API-based backup tool, for all providers.

**Self-hosted Authentik full disaster recovery** additionally needs host-level state the
API can't provide: the Postgres database, the `/data` `/certs` `/media` mounts, and the
`AUTHENTIK_SECRET_KEY`. IdPVault's optional **Full-DR mode** (a per-tenant Postgres URL)
captures an encrypted `pg_dump` alongside each config snapshot — this is the only path that
preserves credential hashes. Bind mounts and the SECRET_KEY remain your host backup's job.

---

## 4. Restore (config)

Snapshots → **Restore…** on any snapshot.

- **Preview (dry-run)** is always safe and read-only. It compares the snapshot to the live
  tenant and shows, per object: create / update / identical, with the changed fields.
- **Apply** writes the changed objects back, dependency-ordered, and produces a per-object
  report. **Apply restores the tenant to the snapshot's state — it is a point-in-time revert,
  not a merge.** Only Authentik apply is implemented today; Okta/Auth0 preview works, apply
  is on the roadmap.
- **Clone / promote**: the "Restore into" selector lets you apply one tenant's snapshot into a
  *different same-provider* tenant (e.g. prod → preview).

---

## 5. Identity backup (users & assignments)

Enable per tenant (Edit → Identity backup: Enabled, set a schedule). Then open the tenant's
**Identity** panel to back up on demand, see snapshots, and preview a restore.

**What's captured:** user profiles + status (no credentials — impossible via API), user→group
memberships, group→app assignments, and **direct** (individually-assigned) user→app
assignments. Group-inherited app access is *not* stored as direct assignments — see §6.

**Rate limits:** Okta limits API calls per org, per minute. IdPVault reads Okta's own
rate-limit headers and automatically throttles — pausing before it hits the limit and backing
off precisely on any 429. It learns your org's real ceiling at runtime (including Workforce
license multipliers), so bigger orgs automatically go faster. Tune the safety margin in
Settings → *Okta rate-limit reserve %* (default 20% headroom, so IdPVault never starves your
org's other integrations).

**Duration & cadence:** after the first identity backup the Identity panel shows the measured
duration and API-call count, and a cadence recommendation. Daily is realistic for the large
majority of orgs, even tens of thousands of users. If a run exceeds ~15 minutes, IdPVault
suggests off-hours scheduling and/or a Workforce multiplier / Okta support increase.

---

## 6. How assignments are preserved (important)

Apps are assigned two ways: **to a group** (members inherit access) or **directly to a user**.
Most admins use groups. IdPVault records the *provenance* of every user's access, so a restore
rebuilds your intended model instead of flattening everything into direct assignments:

Restore order: groups → group→app assignments → user→group memberships → *only* the genuinely
direct user→app assignments. Users who had an app via a group get it back automatically by
being re-added to the group — they are **not** converted into direct assignments.

---

## 7. What's automatic vs. MANUAL after an identity restore

**How identity restore works:** open the tenant's Identity panel → a snapshot → **Restore
preview** (read-only). If anything is missing, **Apply identity restore** writes it back.
Restore is **create-only and additive**: it recreates users that are missing (matched by
login/username), re-adds missing group memberships and assignments, and resolves everything
by natural key so recreated-object ID changes don't break the wiring. It does **not** modify
existing users or delete anything. Idempotent — re-running does nothing if live already matches.

**Automatic:** missing users recreated (profile + status, created STAGED), group memberships
re-added, group→app and direct user→app assignments re-created.

**Manual — you must do these after a restore, because the IdP API makes them impossible to
back up:**

- **Password reset.** Recreated users have no password (credentials are never exportable).
  Send each user a password-reset / activation. IdPVault's restore report lists how many.
- **MFA re-enrollment.** Recreated users must re-enroll their MFA factors (authenticator,
  security key, etc.). Their old factors cannot be restored via API.
- **Secrets on restored config objects** (§3): re-enter OAuth client secrets / upload cert
  private keys where an app or provider needs them.
- **Self-hosted Authentik full recovery:** if you need credentials/MFA preserved (not reset),
  restore from the Full-DR `pg_dump` instead of the API user restore — that path keeps hashes.

The restore preview always spells out the manual steps for that specific restore before you
commit to anything.

---

## 8. Your profile — password & MFA

Click **Profile** in the header. You can change your own password, and enable **two-factor
authentication (TOTP)**: click *Set up MFA*, scan the QR code with an authenticator app
(Google Authenticator, Authy, 1Password) or enter the shown key manually, then verify a code
to turn it on. After that, sign-in asks for a 6-digit code. Disable MFA the same way (requires
a current code). MFA secrets are encrypted at rest with the master key. Admins can reset a user's MFA from the Users page (e.g. lost authenticator). By default MFA is prompted at every sign-in; set a **trusted-device lifetime** in Settings (days) to let a browser skip the code within that window.

## 9. Security

- **Login lockout:** after a configurable number of failed attempts (default 5) an
  account is temporarily locked (default 15 min). Tune both in Settings → Core.
- **Cookies** are HttpOnly and become Secure automatically when you serve IdPVault
  over HTTPS (recommended — put it behind a TLS reverse proxy in production).
- **Headers:** IdPVault sends CSP, X-Frame-Options: DENY, and nosniff on all responses.
- **Self-health:** a daily check alerts you if a scheduled tenant hasn't produced a
  successful backup within the stale-backup window (Settings → Core, default 26h) —
  so a silently failing scheduler or connection doesn't go unnoticed.

## 10. Alerts, audit, metrics

- **Alerts:** IdPVault can notify you on four event types — **configuration drift detected**,
  **backup failed**, **backup succeeded** (off by default, can be noisy), and **restore applied**.
  Choose which to receive with the checkboxes in Settings → Alerts. Delivery is via webhook
  (Slack / Mattermost / Discord — rich attachment format with color + fields — or ntfy) and/or
  email to admins if SMTP is configured. Pick the webhook format (auto-detects by URL) and use
  **Send test alert** to verify. Save before testing.
- **Audit log:** every mutating action (logins, tenant/user/settings changes, restores) is
  recorded and viewable by admins.
- **Metrics:** set `IDPVAULT_METRICS_TOKEN` in the stack to expose a Prometheus `/metrics`
  endpoint (backup age, run counts, storage per tenant).
