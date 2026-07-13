# Security Policy

IdPVault handles identity-provider credentials and configuration backups, so we
take reports seriously and respond fast.

## Reporting a vulnerability

Email **admin@idpvault.com** with a description and reproduction steps.
Please do not open a public issue for security reports. You'll get an
acknowledgment within 48 hours and a fix or mitigation plan within 7 days for
confirmed issues.

## Scope notes

- IdPVault is self-hosted: there is no hosted service, no telemetry, and no
  phone-home. License keys are verified entirely offline.
- All secrets (IdP credentials, snapshots, SMTP password) are encrypted at rest
  with envelope encryption rooted in a master key that never leaves your host.
- The only unauthenticated endpoint is `/healthz`; `/metrics` requires a token.

## Supported versions

The latest released image (`ghcr.io/keltech-services/idpvault:latest`) is
supported. Pin a version tag for reproducible deploys and follow CHANGELOG.md
for security-relevant changes.
