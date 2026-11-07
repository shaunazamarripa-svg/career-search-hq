# Architecture and operating boundary

## Current working implementation

Web UI → FastAPI → SQLite. Each authenticated data request filters by `user_id`. An append-only event log records status changes, approvals, verified receipts, follow-ups, and discovery runs. A standalone `agent.py` imports configured Greenhouse, Lever, and Ashby public employer boards and records outcomes. Optional local MCP tools expose account-scoped read operations and **draft-only** writes for agent assistants.

## Workflow state machine

`Discovered → Verified → Shortlisted → Draft ready → Approval pending → Approved → Submitted → Interview → Offer` (with rejection/withdrawal branches). The API enforces valid transitions. “Submitted” requires a separately approved application plus evidence of an actual submission. Recruiter outreach cannot be created before that confirmation; approval does not mark it sent. Recording sent status requires delivery evidence.

## Integration trust model

External sources and language-model output are untrusted. Before submitting to an employer or sending a LinkedIn message, an agent must obtain current owner approval and use an integration authorized for that platform. The MVP deliberately has **no** browser-based ATS submission, no CAPTCHA bypass, and no recruiter send tool.

## Current limitations and commercial production requirements

This is a functional self-hosted alpha, not a completed subscription SaaS. The current security phase adds persistent login throttling, security headers, session revocation, account deletion, operator provisioning, a consistent backup utility, and an HTTPS pilot Compose recipe. Before inviting public users, still add email verification, password recovery, secrets management, automated off-host backup/restore, observability, tenant billing, abuse controls, privacy policy/terms, tested migrations, and integration-specific legal compliance. Local Compose maps to localhost by default; production Compose publishes only the HTTPS proxy and disables public registration.

## Monetization options

MIT-licensed core remains free. Sell hosted convenience, managed automation, integrations, and support. A downloadable distribution can include setup support or premium proprietary add-ons, while respecting the open-source license and third-party terms. Keep public and paid functionality clearly labeled.
