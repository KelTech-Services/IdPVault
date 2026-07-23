# Security Policy

IdPVault handles identity-provider credentials and configuration backups, so we
take reports seriously and respond fast.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting:
[**Report a vulnerability**](https://github.com/KelTech-Services/IdPVault/security/advisories/new)
(also reachable via Security → Advisories → Report a vulnerability, or the
"Security vulnerability (private)" card on the new-issue chooser). Include a
description and reproduction steps. Please do not open a public issue for
security reports. You'll get an acknowledgment within 48 hours and a fix or
mitigation plan within 7 days for confirmed issues.

## Scope notes

- IdPVault is self-hosted: there is no hosted service and no telemetry. The
  only outbound call the app ever makes on its own is license activation and a
  daily license check-in (license key + random install id only) for activated
  paid licenses; Community tier, legacy keys, and offline license files make no
  network calls. Entitlements are verified offline against an embedded public
  key.
- All secrets (IdP credentials, snapshots, SMTP password) are encrypted at rest
  with envelope encryption rooted in a master key that never leaves your host.
- The only unauthenticated endpoint is `/healthz`; `/metrics` requires a token.

## Supported versions

The latest released image (`ghcr.io/keltech-services/idpvault:latest`) is
supported. Pin a version tag for reproducible deploys and follow CHANGELOG.md
for security-relevant changes.
