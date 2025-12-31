## Provisioning checklist (staging) — `study-staging.hvp.global`

This deploy uses **Cloudflare Pages + Pages Functions** with a **D1** DB binding named `DB`.

### 0) Prereqs
- `hvp.global` is already in Cloudflare.
- This repo is connected to GitHub (`ikohane/surveysays`).
- You can access the Pages project in Cloudflare (account `Kohane@gmail.com's Account`).

### What you get (v0.5 cloud scope)
- Respondent page: `GET /s/<token>`
- API:
  - `GET /api/survey/<token>`
  - `POST /api/submit/<token>` (409 on repeat submission)
- Admin (bearer token):
  - `GET /api/admin/ping`
  - `POST /api/admin/upload` (ingests **bulk invitations JSON** and **returns tokens**)
  - `GET /api/admin/export/<campaignKey>`

### 1) Create D1 database + apply schema
Dashboard:
- Cloudflare Dashboard → **Developer Platform** → **D1**
- **Create database** → name: `surveysays_staging`
- Open the DB → **Console / Query**
- Paste and run [`schema.sql`](schema.sql)

Note: you will need the D1 **Database ID (UUID)** if your Pages project is configured to manage bindings via `wrangler.toml`.

### 2) Create Pages project (monorepo root)
Cloudflare Dashboard → **Workers & Pages** → **Create application** → **Pages**:
- Choose **Import an existing Git repository**
- Select repo: `ikohane/surveysays`
- **Project name**: `surveysays` (or `surveysays-staging`)
- **Production branch**: `main`
- **Build settings**:
  - Framework preset: `None`
  - Build command: *(empty / none)*
  - Build output directory: `public`
  - Root directory (advanced): `cloudflare/pages`

### 3) Bind D1 to Pages Functions (`DB`)
There are **two** ways to bind D1. Your project may be in either mode.

#### Mode A: bindings managed by `wrangler.toml` (common)
You’ll see a tooltip like: “Bindings for this project are being managed through `wrangler.toml`”.

- Ensure [`wrangler.toml`](wrangler.toml) includes the real D1 UUID:
  - `[[d1_databases]] binding="DB" database_name="surveysays_staging" database_id="<UUID>"`
- Commit/push and let Pages redeploy.

#### Mode B: bindings managed in the Dashboard
- Pages project → **Settings** → **Bindings**
- Choose environment: **Production**
- Add D1 binding:
  - **Name**: `DB`
  - **Database**: `surveysays_staging`

### 4) Set Admin token (required)
- Pages project → **Settings** → **Variables and Secrets**
- Add **Secret**:
  - Name: `ADMIN_TOKEN`
  - Value: a long random string

All admin endpoints require:
- `Authorization: Bearer <ADMIN_TOKEN>`

You can generate a good token locally:

```bash
python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
```

### 5) Verify DB binding + auth

```bash
export ADMIN_TOKEN='...'
curl -sS -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  "https://study-staging.hvp.global/api/admin/ping"
```

Expected: `{"ok": true, ...}` and `envKeys` includes `DB`.

### 5) Attach custom domain
- Pages project → **Custom domains** → add `study-staging.hvp.global`
- If Cloudflare shows CNAME instructions, create the record in **Cloudflare DNS** (since DNS is already “Full”):
  - Type: `CNAME`
  - Name: `study-staging`
  - Target: `surveysays.pages.dev`
  - Proxy: **Proxied** (orange cloud)

### Optional (temporary) hardening: WAF IP allow-list for admin endpoints
If Cloudflare Access/Zero Trust is not available yet, you can temporarily protect admin endpoints with a WAF custom rule on the `hvp.global` zone:

- Match: host `study-staging.hvp.global` and path starts with `/api/admin/`
- Block unless request comes from your public IP.

Example expression (IPv4-only):

```text
(http.host eq "study-staging.hvp.global" and starts_with(http.request.uri.path, "/api/admin/") and ip.src ne 24.61.120.91)
```

Important: if your machine uses IPv6, your effective public IP can change frequently. Either allow-list both IPv4 and IPv6, or disable IPv6 on your machine when doing admin actions.

### 6) Upload invitations payload (generates tokens)
From your laptop (where you have the JSON file):

```bash
export ADMIN_TOKEN='...'
curl -sS -X POST \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  --data-binary @your_campaign.bulk_invitations.json \
  "https://study-staging.hvp.global/api/admin/upload"
```

Response includes `{email, token}` pairs. Surveys are at:
- `https://study-staging.hvp.global/s/<token>`

### 7) Export submissions

```bash
export ADMIN_TOKEN='...'
curl -sS \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  "https://study-staging.hvp.global/api/admin/export/<campaignKey>"
```

### Troubleshooting
- **Build log shows an older commit**: “Retry deployment” retries the *same* commit. Deploy the latest `main` commit instead.
- **Error 8000022 Invalid database UUID**: your Pages build is reading a placeholder D1 UUID. Fix the D1 binding (Mode A or B above) and redeploy.
- **`/api/admin/ping` returns `Missing D1 binding 'DB'`**: the D1 binding is not attached to the running environment (often Production vs Preview mismatch), or bindings are `wrangler.toml`-managed but missing the UUID.


