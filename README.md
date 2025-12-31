# SurveySays (Local-first)

**Author:** Isaac Kohane  
**License:** MIT (see `LICENSE`)

SurveySays is a local-first survey authoring and delivery system: you compile questionnaire content locally (from CSVs/templates) into a safe JSON format, manage campaigns and recipients in a local Admin UI, and then deliver surveys by emailing a link where the respondent’s browser retrieves (or is assigned) the questionnaire and submits results back to the study operator. A key mode—recommended by collaborator **Payal Chandak**—is **just-in-time questionnaire configuration on link-open**: when the recipient opens the email link, the system assigns the question set at that moment and snapshots it for reproducibility.

This repo currently contains:

- **`qgen/`**: a local questionnaire generator that produces per-recipient questionnaire JSON variants
- **`admin_app/`**: a local Admin web app (Flask + SQLite) to import CSVs, manage campaigns, generate variants, preview, and export bulk payloads for later Cloudflare upload
- **Database docs**: see [`docs/database/README.md`](docs/database/README.md) for schema and rationale

## Requirements

- Python **3.11+**
- Flask (already available in many Python installs; if not, install it)

## Sample data

- `sample_data/cases.csv`
- `sample_data/recipients.csv`
- `sample_data/templates.csv`
- `sample_data/param_vector.json`

## Run the local Admin web app

From the repo root:

```bash
cd "/Users/zak/Dropbox (Personal)/Coding/SurveySays"
export PYTHONPATH="$(pwd)/qgen"
python3 -m admin_app.admin_app.app
```

Then open `http://127.0.0.1:5055`.

### Typical workflow

- Import `sample_data/cases.csv`
- Import `sample_data/recipients.csv`
- (Optional) Import `sample_data/templates.csv` if using template-based generation
- Create a campaign (e.g. `demo_campaign`, seed `12345`, version `1`, picker strategy, K)
- If using `template_expand`: open the campaign and upload `sample_data/param_vector.json`
- Click **Generate**
- Click **Preview** and **Stats**
- Click **Download bulk JSON** (this is the payload intended for later Cloudflare ingestion)

### Online assignment mode (assignment on link-open)

If you set a campaign’s picker strategy to **`online_assign`**:

- Clicking **Prepare** creates:
  - **Invitations** (one per recipient, each with a stable token)
  - A **question bank** (from `cases.csv`)
- Opening a respondent link **`/s/<token>`** performs **just-in-time questionnaire configuration**: it assigns **K** question items at first open, builds the questionnaire JSON, and **snapshots** the resulting `questionnaire_json` onto that invitation (subsequent opens are idempotent).
- Export tokens via **Download invitations JSON** (endpoint: `"/campaigns/<campaign_key>/export_invitations.json"`).

This “assignment on first link-open” mode was added to satisfy a collaborator specification from **Payal Chandak**.

## Submission (online_assign)

In `online_assign` mode, respondents submit exactly once per token.

- **Submit endpoint**: `POST /s/<token>/submit`
- **Payload (conceptual, 1A answers map)**:
  - `answers: { [blockId]: value }`
  - where `value` is:
    - `singleSelect`: a `choice.id` string
    - `freeText`: a string
- **Repeat submits**: return **HTTP 409 Conflict** (first submit wins).

The local simulated respondent page (`/s/<token>`) renders an HTML form and posts to the submit endpoint.

## Results (local)

- Master view includes campaign-level counts and tables.
- Results page: `/campaigns/<campaign_key>/results` shows aggregated `singleSelect` counts and recent `freeText` answers.

## Next steps (Cloudflare + Resend)

From the project plan (`.cursor/plans/qgen-local-admin.plan.md`), the next parallel steps to take once local authoring is stable:

- **Cloudflare**: deploy a staging site on `study-staging.hvp.global` using Cloudflare Pages + Pages Functions + D1, then add Cloudflare Access to protect admin workflows. See the Cloudflare docs in [`cloudflare/pages/PROVISIONING.md`](cloudflare/pages/PROVISIONING.md).
- **Resend**: verify domain + sender for deliverability so the system can email invitations at scale.

## Cloudflare (staging, v0.5)

We ship a minimal cloud slice for v0.5: **respondent + API only** (no cloud Admin UI yet). You continue to use the local Admin app to generate a bulk invitations JSON payload, then upload it to Cloudflare which returns per-email tokens.

- **Docs**: [`cloudflare/pages/PROVISIONING.md`](cloudflare/pages/PROVISIONING.md)
- **Respondent**:
  - `GET /s/<token>` (survey page)
- **API**:
  - `GET /api/survey/<token>`
  - `POST /api/submit/<token>` (returns **409** on repeat submission)
- **Admin (bearer)**:
  - `GET /api/admin/ping`
  - `POST /api/admin/upload` (ingests bulk payload, returns `{email, token}`)
  - `GET /api/admin/export/<campaignKey>`

### Programmatic push from the local Admin app (no manual download/upload)

Set these env vars in the same shell where you run the local Admin server:
- `CLOUDFLARE_STUDY_BASE_URL` (example: `https://study-staging.hvp.global`)
- `CLOUDFLARE_ADMIN_TOKEN` (the Pages `ADMIN_TOKEN` secret)

Then open the campaign **Master view** and use **“Push to Cloudflare (generate tokens)”**. The local app will:\n+- Build the bulk invitations payload in-memory\n+- POST to `{{CLOUDFLARE_STUDY_BASE_URL}}/api/admin/upload`\n+- Store the returned `{email, token}` mapping locally and show it as a ledger\n+- Provide a **Download cloud tokens CSV** button for emailing
Then open the campaign **Master view** and use **“Push to Cloudflare (generate tokens)”**. The local app will:

- Build the bulk invitations payload in-memory
- POST to `${CLOUDFLARE_STUDY_BASE_URL}/api/admin/upload`
- Store the returned `{email, token}` mapping locally and show it as a ledger
- Provide a **Download cloud tokens CSV** button for emailing

Note: your staging currently has an additional Cloudflare WAF rule (IP allow-list) for `/api/admin/*`, so pushing will only work from an allowed IP.

#### TLS / certificate errors (macOS/Homebrew Python)
If you see `CERTIFICATE_VERIFY_FAILED` when pushing to Cloudflare, upgrade/install `certifi` and re-run. The Admin app prefers `certifi`’s CA bundle for outbound HTTPS.

## Run qGen directly (CLI)

The CLI currently supports **Pick-K from `cases.csv`** (default) and outputs **K question pairs** per recipient via `--k`.

```bash
cd "/Users/zak/Dropbox (Personal)/Coding/SurveySays"
PYTHONPATH="$(pwd)/qgen" python3 -m qgen \
  --campaign-key demo_campaign \
  --title "Clinical Case Decision Survey" \
  --version 1 \
  --seed 12345 \
  --cases-csv sample_data/cases.csv \
  --recipients-csv sample_data/recipients.csv \
  --out out/bulk_invitations.json
```

To generate **K=2** question pairs per recipient:

```bash
cd "/Users/zak/Dropbox (Personal)/Coding/SurveySays"
PYTHONPATH="$(pwd)/qgen" python3 -m qgen \
  --campaign-key demo_campaign \
  --title "Clinical Case Decision Survey" \
  --version 1 \
  --seed 12345 \
  --cases-csv sample_data/cases.csv \
  --recipients-csv sample_data/recipients.csv \
  --k 2 \
  --out out/bulk_invitations.json
```

## CSV contracts

### `cases.csv`

Required columns:
- `case_id`
- `vignette`
- `prompt`

Choices: provide either
- `choices_json`: JSON array like `[{"id":"A","label":"..."}, ...]`, or
- columns named `choice_*` (e.g. `choice_A`, `choice_B`, ...). The suffix becomes the choice id.

Optional:
- `tags`: pipe-separated, e.g. `cardio|adult`

### `recipients.csv`

Required columns:
- `email`
- `firstname`
- `lastname`

All other columns become `recipientStrata` metadata.

### `templates.csv` (for `template_expand`)

Required columns:
- `template_id`
- `vignette_template` (may include `{var}` placeholders)
- `prompt_template` (may include `{var}` placeholders)

Choices: provide either
- `choices_json`: JSON array like `[{"id":"A","label":"..."}]` (labels may include `{var}` placeholders), or
- columns named `choice_*` (e.g. `choice_A`, `choice_B`, ...). Labels may include `{var}` placeholders.

Optional:
- `tags`: pipe-separated, e.g. `template|demo`
- `rules_yaml`: inline YAML defining per-template variable selection rules

### `param_vector.json` (for `template_expand`)

MVP schema:

```json
{
  "pools": {
    "varName": ["v1", "v2"],
    "age": [24, 37]
  }
}
```

The template `rules_yaml` references these pools.

## Output format (bulk invitations JSON)

The exported/downloaded JSON looks like:

```json
{
  "campaignKey": "demo_campaign",
  "invitations": [
    {
      "email": "alice@example.com",
      "questionnaireVersion": 1,
      "questionnaireJson": { "title": "...", "questionnaireVersion": 1, "blocks": [/* ... */] },
      "metadata": {
        "recipientStrata": { "site": "SiteA" },
        "seed": 12345,
        "k": 2,
        "pickerStrategy": "pick_k_cases",
        "units": [
          { "caseId": "case_001", "caseTags": ["cardio","adult"] },
          { "caseId": "case_002", "caseTags": ["neuro","adult"] }
        ],
        "questionnaireHash": "..."
      }
    }
  ]
}
```

## Questionnaire JSON blocks (MVP)

Blocks are rendered in order. Current supported block types:

- **`vignette`**: `{ type: "vignette", id, text }`
- **`singleSelect`**: `{ type: "singleSelect", id, prompt, required: true, choices: [{id,label}, ...] }`
- **`freeText` (MVP)**: `{ type: "freeText", id, prompt, required: true }` (short, single-line)


