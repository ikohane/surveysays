---
name: qGen + Local Admin App
overview: Build qGen (Python) plus a local Admin web app (Python server + SQLite) that imports cases/recipients from CSV, manages campaigns, generates per-recipient questionnaire variants in-process, previews them, and exports bulk invitation payloads for later Cloudflare upload and email sending.
todos:
  - id: csv-contracts
    content: Confirm required columns for cases.csv and recipients.csv; implement CSV parsers with validation and helpful errors.
    status: pending
  - id: qgen-core
    content: Implement qGen core models + validator + generator for vignette + singleSelect decision question, including seeded reproducibility and optional hash.
    status: pending
    dependencies:
      - csv-contracts
  - id: admin-sqlite
    content: Implement SQLite schema and persistence for campaigns, cases, recipients, and generated invitation variants.
    status: pending
    dependencies:
      - csv-contracts
  - id: admin-ui
    content: "Implement local Admin web UI: import CSVs, create campaign, generate variants in-process, preview variants, view stats."
    status: pending
    dependencies:
      - qgen-core
      - admin-sqlite
  - id: export-bulk-json
    content: Export/download bulk invitations JSON from the Admin UI (and validate before export).
    status: pending
    dependencies:
      - admin-ui
  - id: docs-local-run
    content: Document how to run qGen + Admin app locally, including sample CSVs and a minimal walkthrough.
    status: pending
    dependencies:
      - export-bulk-json
---

# qGen + local Admin web app plan

## Goal

Start locally by implementing:

- **qGen**: a Python library (and optional CLI) that generates **per-recipient questionnaire JSON** variants (clinical vignette + single decision choice).
- **Local Admin web app**: a Python web app with **SQLite persistence** that imports **`cases.csv` + `recipients.csv`**, manages campaigns, runs qGen generation **in-process**, previews variants, and exports a **bulk invitations JSON** payload that will later be uploaded to Cloudflare.

## Architecture (local-first)

- **Python monorepo** with two entrypoints:
- `qgen/` package: generation + schema + validation
- `admin_app/` package: local web server + SQLite + UI + calls into `qgen`

## Data contracts (contract-first)

### Questionnaire JSON (stored per invitation/variant)

Minimal schema (MVP):

- `title: string`
- `questionnaireVersion: number`
- `blocks: Array<VignetteBlock | SingleSelectQuestion>`

`VignetteBlock`:

- `type: "vignette"`
- `id: string`
- `text: string`

`SingleSelectQuestion`:

- `type: "singleSelect"`
- `id: string`
- `prompt: string`
- `required: true`
- `choices: Array<{ id: string, label: string }>`

### Bulk invitations export (what Cloudflare will ingest later)

- `{ campaignKey, invitations: [{ email, questionnaireVersion, questionnaireJson, metadata }] }`

## CSV imports (local Admin web app)

- `cases.csv` (qGen inputs): includes case id, vignette text, choice set, optional tags/strata.
- `recipients.csv`: includes email and optional stratification columns.

(We’ll define exact required columns once you share one example row for each CSV.)

## Local Admin web app features (MVP)

- **Campaign management**:
- create/select campaign, set title, seed, version
- import/refresh `cases.csv` and `recipients.csv`
- **Generation controls**:
- generate per-recipient variants (seeded and reproducible)
- optional dedup hash / distribution stats
- **Preview**:
- view a generated variant rendered safely (DOM APIs; no HTML injection)
- inspect the computed questionnaire JSON
- **Export**:
- download bulk invitations JSON for later upload

## Local persistence

- SQLite DB storing:
- campaigns
- imported cases
- imported recipients
- generated invitations/variants (including questionnaire snapshot + hash)

## Parallel cloud work (you can do while local is built)

- Cloudflare: add `hvp.global` zone, plan for `study.hvp.global` Pages + Access.
- Resend: verify domain + sender for deliverability.

## Acceptance criteria (local)

- Import both CSVs successfully and persist to SQLite.
- Generate N per-recipient variants deterministically from a seed.
- Preview at least 10 variants in the web UI.
- Export a bulk invitations JSON that validates against the qGen schema.

## Key files we will add

- `qgen/pyproject.toml`
- `qgen/qgen/models.py`, `qgen/qgen/schema.py`, `qgen/qgen/generate.py`, `qgen/qgen/variants.py`
- `admin_app/admin_app/app.py` (or similar), `admin_app/admin_app/db.py`, `admin_app/admin_app/templates/`, `admin_app/admin_app/static/`
- `README.md`


