# SurveySays (Local-first)

**Author:** Isaac Kohane  
**License:** MIT (see `LICENSE`)

This repo currently contains:

- **`qgen/`**: a local questionnaire generator that produces per-recipient questionnaire JSON variants
- **`admin_app/`**: a local Admin web app (Flask + SQLite) to import CSVs, manage campaigns, generate variants, preview, and export bulk payloads for later Cloudflare upload

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
- Opening a respondent link **`/s/<token>`** performs **first-open assignment** and **snapshots** the resulting `questionnaire_json` onto that invitation (subsequent opens are idempotent).
- Export tokens via **Download invitations JSON** (endpoint: `"/campaigns/<campaign_key>/export_invitations.json"`).

This “assignment on first link-open” mode was added to satisfy a collaborator specification from **Payal Chandak**.

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

Required column:
- `email`

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


