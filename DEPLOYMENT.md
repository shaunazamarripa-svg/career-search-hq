# Production pilot deployment (not yet a public signup service)

This secure deployment recipe targets a Linux server with Docker Compose, one domain, HTTPS, and an off-host backup destination. It does not make Career Search HQ a commercially audited SaaS.

## Prerequisites

1. Server with Docker Engine and Compose plugin, secure operator SSH, patched OS, and firewall allowing only inbound 80/443 and controlled SSH.
2. A DNS A/AAAA record for the public hostname pointing to that server.
3. A decision on identity provider, billing, and privacy terms before enabling public account creation.
4. Off-host encrypted backups and a tested restore procedure. Docker volumes are not backups.

## Pilot deployment

```bash
cp .env.production.example .env
# Edit DOMAIN to the actual public hostname.
docker compose -f compose.production.yaml up -d --build
docker compose -f compose.production.yaml exec hq python manage.py create-user pilot@example.com
docker compose -f compose.production.yaml ps
```

Caddy terminates HTTPS and manages TLS certificates when DNS and inbound ports are correctly configured. Only Caddy publishes ports; FastAPI stays on an internal network. The application validates the Host header against `DOMAIN`, sends security headers and HSTS, and disables public registration (`ALLOW_PUBLIC_SIGNUP=0`). Use the operator command to create pilot accounts.

## Scheduled discovery

The worker runs configured Greenhouse, Lever, and Ashby employer board scans daily at `WORKER_HOUR` in `WORKER_TZ`. Logs and per-user `agent_runs` record results. It never submits applications or sends recruiter messages.

## Backups

```bash
docker compose -f compose.production.yaml exec hq python manage.py backup /data/backup-example.db
```

The SQLite backup API creates a consistent file inside the data volume. Transfer it to encrypted off-host storage with your approved backup system. Backups include personal data and password hashes. Perform and document a staging restore drill before production use.

## Before external customer launch

- Email verification, password reset, MFA/SSO option, anti-abuse registration protection.
- Independent security review for session handling, authorization, XSS, SSRF, and rate limiting.
- Managed backup/restore and tested migrations, monitoring, incident response, release rollback.
- Consent and retention controls for resumes, candidate profiles, recruiter contacts, and imported listings.
- Billing provider integration, subscription cancellation/refund handling, metering, and entitlements.
- Real HTTPS staging acceptance with Docker build and Caddy before production use.

CI tests the API; it is not a penetration test or a substitute for live infrastructure acceptance.
