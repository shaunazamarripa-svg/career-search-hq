# Career Search HQ

An open-source, self-hosted job-search command center with user-owned data and approval-first workflows. Individual users can set different career titles, salary thresholds, work arrangements, deal-breakers, and scoring weights.

## What works now

- Local accounts, password hashing, token sessions, per-user data isolation
- Guided configuration, editable job pipeline, canonical URL deduplication
- 100-point customizable job-fit scoring with hard-filter rejection
- Applications and recruiter messages with **separate explicit approvals**
- No fabricated submission receipts: approval does not equal sending
- Contact records, follow-up due dates, outcome metrics, append-only events
- CSV import/export and Greenhouse board job ingestion (board identifiers provided by user)
- Unattended Greenhouse discovery worker for opt-in employer board lists (`python agent.py`, schedule with cron)
- Generated weekly scorecard, overdue reminders, and read-only agent API
- Local MCP bridge exposing account-scoped job reading, opportunity intake, draft preparation and weekly review

## Run locally

```bash
python -m pip install -r requirements.txt
uvicorn app:app --reload --port 8080
```

Visit http://localhost:8080. Or use `docker compose up --build` for self-hosting.

For internet deployment, put it behind HTTPS, set up managed backups and monitoring, and configure an email-verification/password-reset provider before inviting strangers. The included authentication is an MVP, not a commercially audited identity stack.

## Product boundaries

- A job can be *approved*, but it becomes *submitted* only when the user records a verified employer/ATS receipt or an authorized adapter returns one.
- LinkedIn sending and employer ATS submissions are not implemented. They require separate permitted integrations and explicit user consent. Never bypass CAPTCHA or platform restrictions.
- No unsupported "AI match score" claims: the score is deterministic and explainable.
- External job feeds vary in completeness. Source verification remains a separate state.
- This repository includes **no Stripe billing, proprietary job-board feeds, or multi-tenant SaaS infrastructure**. Hosted subscriptions are a future commercial layer, not a finished capability.

## API

After registering, a bearer token is issued in `/api/auth/register`; login at `/api/auth/login`. `Authorization: Bearer <token>` is accepted for programmatic agent access. UI keeps the token in session storage. Key endpoints:

| Route | Action |
|---|---|
| `GET/PUT /api/settings` | Career criteria and weights |
| `GET/POST /api/jobs` | Pipeline and intake |
| `POST /api/jobs/{id}/status` | Verified application state changes |
| `POST /api/jobs/{id}/application` | Draft application and approval |
| `POST /api/jobs/{id}/outreach` | Draft outreach and approval |
| `POST /api/jobs/{id}/contacts` | Verified recruiter contact |
| `GET /api/followups` | Due tasks |
| `POST /api/followups` | Schedule follow-up |
| `GET /api/metrics` | Activity and funnel KPI |
| `GET /api/events` | Audit trail |
| `POST /api/import/csv` | CSV upsert |
| `GET /api/export/csv` | CSV export |
| `POST /api/discovery/greenhouse` | Employer board discovery |
| `POST /api/discovery/run` | Trigger configured board scans |
| `GET /api/agents/runs` | Scan evidence, errors and counts |

All mutable records belong to the authenticated account. See `tests/test_flow.py` for a tested workflow.

## Run the agents

In Search Criteria, add the Greenhouse board IDs for employers you want to follow. Start a manual scan from Data Portability or schedule the command below on your own host (for example, a daily `cron` entry):

```bash
python agent.py
```

The command logs each actual scan result. It cannot discover every job board across the internet or autonomously submit applications. Additional connectors must be implemented and verified one by one.

Optional MCP bridge for Claude Code and other local MCP-compatible clients:

```bash
python -m pip install -r requirements-mcp.txt
# First log in to your local Career Search HQ API to obtain your personal session token.
export HQ_URL=http://127.0.0.1:8080
export HQ_TOKEN=YOUR_PRIVATE_SESSION_TOKEN
python mcp_server.py
```

Register the `mcp_server.py` command in your MCP client's local configuration with `HQ_URL` and `HQ_TOKEN` supplied as private environment variables. Never commit tokens. This server intentionally supports draft-only writes; approval and actual sending remain with the user. The session token expires after seven days and must then be renewed.

## License and business model

MIT license. Self-hosted source can be copied and sold subject to the license notice terms; a hosted product can charge for convenience and integrations. The hosted service must differentiate itself with reliable connectors, operations, security, support, and automation—not by pretending the free code is exclusive.

## Planned extensions

1. Production identity and password recovery; encrypted credential vault and background worker.
2. ATS-specific adapters with receipt verification and failure tracking.
3. Authenticated inbox sync; opt-in recruiter search and separate outreach confirmation.
4. Editable resume/cover-letter artifacts and versioned evidence bank.
5. SaaS tenant management, metering, Stripe subscriptions, backups, abuse controls.

## Video agent (optional, local, no hosted video API)

Kinocut is an Apache-2.0 local FFmpeg MCP editor. Claude Code command (with FFmpeg available):

```bash
claude mcp add kinocut -- uvx --from kinocut kino --mcp
```

This is installation guidance, not a claim that this repository or your Claude client is already connected. See https://github.com/KyaniteLabs/kinocut.
