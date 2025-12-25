# Database README (Local Admin SQLite)

**Author:** Isaac Kohane  
**License:** MIT (see repo `LICENSE`)

This project’s local Admin app persists state in a single SQLite database (default: `out/local_admin.sqlite3`). The schema is designed around two complementary flows:

- **Pre-generated variants**: qGen produces a full `questionnaire_json` per recipient up-front and stores it in `invitation_variants`.
- **Just-in-time assignment on link-open** (**Payal Chandak** recommendation): the system emails a tokenized link; when the recipient opens the link, the system assigns the question set at that moment, records the assignment, and snapshots the resulting `questionnaire_json` onto `invitations` for reproducibility.

The schema lives in `admin_app/admin_app/db.py` (`SCHEMA_SQL`) and is created/updated automatically when the app starts.

## High-level entities

- **Campaign**: a named study run with a fixed seed/version and a picker strategy.
- **Content sources**:
  - **Cases**: vignette + prompt + choices (from `cases.csv`)
  - **Templates**: template text + rules + choices (from `templates.csv`) used only for offline `template_expand`
- **Recipients**: emails + optional strata (from `recipients.csv`)
- **Invitation / token**: one per recipient (online mode) used in emailed links.
- **Question bank items**: the set of assignable question items for a campaign (online mode).

## Tables (summary)

### `campaigns`

One row per campaign.

- **Key columns**
  - `campaign_key` (unique): stable external identifier
  - `seed`, `questionnaire_version`: reproducibility controls
  - `picker_strategy`: `pick_k_cases` | `template_expand` | `online_assign`
  - `k`: questions per recipient (for all strategies)
  - `param_vector_json`: only used by `template_expand`

### `cases`

Imported “atomic” question units (vignette + prompt + choices + tags).

- `case_id` is unique and is referenced in metadata for offline variant generation.

### `templates`

Imported templates and per-template rules used only by the offline `template_expand` strategy.

### `recipients`

Imported recipients.

- `email` is unique
- `strata_json` stores any extra CSV columns as JSON

### Offline (pre-generated) mode tables

#### `invitation_variants`

Stores one pre-generated questionnaire per recipient per campaign.

- **Why it exists**
  - Enables “compile everything locally, then upload” workflows
  - Makes export a pure DB read (`/campaigns/<key>/export.json`)
- **Important columns**
  - `questionnaire_json`: the full rendered JSON snapshot
  - `questionnaire_hash`: stable hash for grouping identical questionnaires
  - `metadata_json`: includes seed, picker strategy, and which cases/templates were used

### Online (link-open / just-in-time assignment) tables

#### `invitations`

Stores email + token and (after first link-open) the snapshotted questionnaire.

- **Why it exists**
  - Allows forwarding: the token is the bearer credential
  - Makes link-open idempotent: once snapshotted, subsequent opens return the same questionnaire
- **Important columns**
  - `token` (unique): used in `/s/<token>`
  - `opened_at`: set on first open
  - `questionnaire_json` / `questionnaire_hash`: filled on first open (snapshot)
- **Uniqueness**
  - `(campaign_id, email)` is unique: one invite per recipient per campaign

#### `question_items`

The campaign’s assignable question bank snapshot.

- In current MVP, items are built from `cases` with stable IDs of the form `case:<case_id>`.
- Columns `vignette/prompt/choices_json/tags_json` are copied into the bank so assignment remains reproducible even if the source CSV changes later.

#### `question_stats`

Exposure counters per `question_items` row.

- `assigned_count`: incremented when an item is assigned at link-open time
- `submitted_count`: reserved for later (increment on response submit)

#### `respondent_assignments`

The chosen items for a given invitation token (ordered by `position`).

- This is the authoritative mapping from **token → set of question items**.

## Relationships / data flow

- **Offline path**
  - `cases` / `templates` / `recipients` → generator → `invitation_variants`
- **Online path (Payal Chandak link-open mode)**
  - `cases` / `recipients` → “Prepare” → `invitations` + `question_items` + `question_stats`
  - First `GET /s/<token>`:
    - chooses K items (lowest `assigned_count`, deterministic tie-break per token)
    - writes `respondent_assignments`
    - increments `question_stats.assigned_count`
    - snapshots `questionnaire_json` into `invitations`

## Useful queries

### For online mode: hash → which question items (ordered)

```sql
SELECT
  i.questionnaire_hash,
  ra.position,
  ra.item_id,
  qi.source_kind,
  qi.source_id
FROM invitations i
JOIN respondent_assignments ra
  ON ra.campaign_id = i.campaign_id AND ra.token = i.token
JOIN question_items qi
  ON qi.campaign_id = i.campaign_id AND qi.item_id = ra.item_id
WHERE i.campaign_id = ? AND i.questionnaire_hash = ?
ORDER BY ra.position;
```

### For online mode: token → snapshotted questionnaire JSON

```sql
SELECT questionnaire_json
FROM invitations
WHERE token = ?;
```

### For offline mode: hash → all recipients who got it

```sql
SELECT email, case_id, created_at
FROM invitation_variants
WHERE campaign_id = ? AND questionnaire_hash = ?
ORDER BY email;
```

## Design rationale (why this schema looks like this)

- **Reproducibility**
  - Offline mode stores the full `questionnaire_json` on `invitation_variants`.
  - Online mode snapshots the full `questionnaire_json` on `invitations` on first open.
- **Idempotency**
  - Online mode uses “snapshot if missing” logic so forwarding/re-opening is safe and consistent.
- **Auditability**
  - `respondent_assignments` preserves the exact item IDs chosen for each token.
  - `question_stats` provides running exposure counts to support balancing strategies.
- **Future cloud parity**
  - This schema is intentionally SQLite-friendly so it can map cleanly to Cloudflare D1 later (with minimal translation).


