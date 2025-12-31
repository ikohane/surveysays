## SurveySays Cloudflare (Pages + Pages Functions) — staging

This folder contains the **Cloudflare deployment** for SurveySays using **Cloudflare Pages + Pages Functions** (same-origin) with a **D1** database.

### What’s in v0.5 cloud scope
- **Respondent + API only** (no Admin UI in the browser yet)
- You continue to use the **local Admin app** to import CSVs and generate `BulkInvitationsPayload` JSON.
- You upload that payload to Cloudflare via an **admin-protected** endpoint.

### Endpoints
- `GET /s/<token>`: respondent HTML page
- `GET /api/survey/<token>`: returns questionnaire JSON snapshot for that token
- `POST /api/submit/<token>`: accepts the 1A “answers map” and stores a one-and-done submission (409 on repeat)
- `POST /api/admin/upload`: upload `BulkInvitationsPayload` JSON (requires admin bearer token)
-   - Note: `BulkInvitationsPayload` from the local Admin app **does not include tokens**. This endpoint will **generate tokens** and return `{email, token}` pairs so you can send emails pointing at `/s/<token>`.
- `GET /api/admin/export/<campaignKey>`: export submissions as JSON (requires admin bearer token)

### Secrets / config
Set these as **Pages environment variables** (Settings → Environment variables):
- `ADMIN_TOKEN`: bearer token required for `/api/admin/*`

### D1
This Pages project expects a D1 binding named `DB` (see `wrangler.toml`) and schema in `schema.sql`.


