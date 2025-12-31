## Provisioning checklist (staging) — `study-staging.hvp.global`

This deploy uses **Cloudflare Pages + Pages Functions** with a **D1** DB binding named `DB`.

### 0) Prereqs
- `hvp.global` is already in Cloudflare.
- This repo is connected to GitHub (`ikohane/surveysays`).

### 1) Create D1 database + apply schema
Option A (Dashboard):
- Cloudflare Dashboard → **D1** → **Create database** → name `surveysays_staging`
- Open the DB → **Console** → paste and run [`schema.sql`](schema.sql)

Option B (wrangler CLI):
- Install `wrangler` (v3+)
- Create DB:
  - `wrangler d1 create surveysays_staging`
- Copy the returned `database_id` into [`wrangler.toml`](wrangler.toml)
- Apply schema:
  - `wrangler d1 execute surveysays_staging --file=./schema.sql`

### 2) Create Pages project (monorepo root)
- Cloudflare Dashboard → **Pages** → **Create a project** → connect GitHub → select `surveysays`
- **Root directory**: `cloudflare/pages`
- **Build command**: (empty / none)
- **Build output directory**: `public`

### 3) Bind D1 to Pages
- Pages project → **Settings** → **Functions** → **D1 database bindings**
- Add binding:
  - Variable name: `DB`
  - Database: `surveysays_staging`

### 4) Set Admin token (required)
- Pages project → **Settings** → **Environment variables**
- Add variable:
  - `ADMIN_TOKEN`: choose a long random string

All admin endpoints require:
- `Authorization: Bearer <ADMIN_TOKEN>`

### 5) Attach custom domain
- Pages project → **Custom domains** → add `study-staging.hvp.global`
- If prompted, Cloudflare will create the needed DNS record.

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


