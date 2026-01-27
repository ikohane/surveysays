# SurveySays - Developer Documentation

**Author:** Isaac Kohane  
**License:** MIT

Technical documentation for developers, contributors, and DevOps engineers.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [System Components](#system-components)
- [Database Schema](#database-schema)
- [API Endpoints](#api-endpoints)
- [CSV Contracts](#csv-contracts)
- [Questionnaire JSON Format](#questionnaire-json-format)
- [Deployment Infrastructure](#deployment-infrastructure)
- [Development Setup](#development-setup)
- [Testing](#testing)

---

## Architecture Overview

SurveySays is a **local-first** survey authoring and delivery system. You compile questionnaire content locally (from CSVs/templates) into a safe JSON format, manage campaigns and recipients in a local Admin UI, and then deliver surveys by emailing a link where the respondent's browser retrieves (or is assigned) the questionnaire and submits results back to the study operator.

### Key Design Principle

A key mode—recommended by collaborator **Payal Chandak**—is **just-in-time questionnaire configuration on link-open**: when the recipient opens the email link, the system assigns the question set at that moment and snapshots it for reproducibility.

### Data Flow

```
Local Admin (Flask + SQLite)
  ↓ Generate/Prepare
  ↓ 
Question Bank + Recipients
  ↓ Push to Cloud
  ↓
Cloud Platform (Cloudflare/Railway)
  → Email Invitations (Resend)
  → Respondents open links
  → Submit responses
  ↓ Sync submissions
Local SQLite (analysis & export)
```

---

## System Components

### `qgen/` - Question Generator Library

Python library for generating per-recipient questionnaire JSON variants.

**Key Modules:**
- `qgen/contracts.py` - TypedDict definitions for questionnaire JSON
- `qgen/generator.py` - Core generation logic
- `qgen/qpicker/` - Question selection strategies
  - `pick_k_cases.py` - Random K selection with deterministic seeding
  - `template_expand.py` - Template-based generation with variables
- `qgen/io_csv.py` - CSV parsing for cases and recipients
- `qgen/validation.py` - Questionnaire JSON validation

**Picker Strategies:**
1. **pick_k_cases**: Randomly select K questions per recipient
   - Uses campaign seed for deterministic shuffling
   - Per-recipient offset for load distribution
   - Supports deduplication within recipient
2. **template_expand**: Generate from templates with variable substitution
3. **online_assign**: Just-in-time assignment on link open

### `admin_app/` - Local Admin Web Interface

Flask application with SQLite database for campaign management.

**Structure:**
- `admin_app/app.py` - Flask app factory
- `admin_app/db.py` - Database functions and queries
- `admin_app/logic.py` - Business logic (assignment, sync, etc.)
- `admin_app/routes/all_routes.py` - All HTTP endpoints
- `admin_app/templates/` - Jinja2 templates with Basic/Expert mode support
- `admin_app/resend_client.py` - Resend API integration
- `admin_app/utils.py` - Utilities (config, JSON, cloud API calls)

**Key Features:**
- Dual UI mode (Basic/Expert) with session-based toggle
- Local/Cloud admin mode toggle
- CSV import for cases, recipients, templates
- Variant generation and preview
- Email template editing with live preview
- Cloud push integration (Cloudflare/Railway)
- Submission syncing from cloud platforms

### `cloudflare/pages/` - Cloud Respondent Delivery

Cloudflare Pages + Pages Functions + D1 database for production hosting.

**Endpoints:**
- `GET /s/<token>` - Survey page for respondents
- `POST /api/submit/<token>` - Submission endpoint (409 on repeat)
- `GET /api/survey/<token>` - Get questionnaire JSON
- `POST /api/admin/upload` - Bulk invitation upload (bearer auth)
- `GET /api/admin/export/<campaignKey>` - Export submissions (bearer auth)
- `GET /api/admin/ping` - Health check (bearer auth)

**See:** [cloudflare/pages/PROVISIONING.md](cloudflare/pages/PROVISIONING.md)

### Railway Deployment

Railway.app hosting for Dynamic Assignment campaigns with PostgreSQL.

**Features:**
- Real-time question assignment on link open
- PostgreSQL database for question bank and stats
- Load-balanced question distribution
- Auto-deploy from GitHub

**See:** [docs/RAILWAY_DEPLOYMENT.md](docs/RAILWAY_DEPLOYMENT.md)

---

## Database Schema

### Local SQLite Schema

**Core Tables:**

**`campaigns`**
- `id` INTEGER PRIMARY KEY
- `campaign_key` TEXT UNIQUE
- `title` TEXT
- `seed` INTEGER
- `questionnaire_version` INTEGER
- `picker_strategy` TEXT (pick_k_cases, online_assign, template_expand)
- `k` INTEGER (questions per recipient)
- `email_yaml` TEXT (email configuration)
- `layout_yaml` TEXT (layout configuration)
- `email_from`, `email_subject`, `email_base_url`, `email_html` TEXT (parsed from YAML)

**`cases`**
- `id` INTEGER PRIMARY KEY
- `case_id` TEXT UNIQUE
- `vignette` TEXT
- `prompt` TEXT
- `choices_json` TEXT (JSON array)
- `tags` TEXT (pipe-separated)

**`recipients`**
- `id` INTEGER PRIMARY KEY
- `email` TEXT UNIQUE
- `strata_json` TEXT (includes firstname, lastname, custom fields)

**`templates`** (for template_expand strategy)
- `id` INTEGER PRIMARY KEY
- `template_id` TEXT UNIQUE
- `vignette_template` TEXT
- `prompt_template` TEXT
- `choices_json` TEXT
- `tags` TEXT
- `rules_yaml` TEXT

**Campaign-Specific Tables:**

**`invitations`** (for online_assign)
- `id` INTEGER PRIMARY KEY
- `campaign_id` INTEGER REFERENCES campaigns(id)
- `token` TEXT UNIQUE
- `email` TEXT
- `opened_at` TEXT (ISO 8601 timestamp)
- `questionnaire_json` TEXT (snapshotted on first open)
- `questionnaire_hash` TEXT

**`question_items`** (for online_assign)
- Question bank for just-in-time assignment
- `campaign_id`, `item_id`, `source_kind`, `source_id`
- `vignette`, `prompt`, `choices_json`, `tags_json`

**`question_stats`** (for online_assign)
- `campaign_id`, `item_id`
- `assigned_count`, `submitted_count`

**`respondent_assignments`** (for online_assign)
- `campaign_id`, `token`, `item_id`, `position`
- Tracks which questions assigned to which token

**`variants`** (for pick_k_cases)
- Pre-generated questionnaire JSON per recipient
- `campaign_id`, `recipient_id`, `variant_json`, `variant_hash`

**`generation_waves`**
- Tracks question generation events
- `campaign_id`, `wave_number`, `created_at`
- `picker_strategy`, `k`, `seed`, `recipients_processed`, `variants_created`

**`submissions`**
- `id` INTEGER PRIMARY KEY
- `campaign_id` INTEGER
- `token` TEXT
- `submitted_at` TEXT (ISO 8601)
- `questionnaire_hash` TEXT
- `cloud_base_url` TEXT (for cloud submissions)

**`submission_answers`**
- `id` INTEGER PRIMARY KEY
- `submission_id` INTEGER REFERENCES submissions(id)
- `block_id` TEXT
- `block_type` TEXT (singleSelect, freeText)
- `value_text` TEXT
- `value_choice_id` TEXT

**Complete Schema:** See [docs/database/README.md](docs/database/README.md)

---

## API Endpoints

### Local Admin API

**Campaign Management:**
- `GET /` - Home page
- `POST /create-campaign` - Create new campaign
- `GET /campaigns/<campaign_key>` - Campaign configuration
- `GET /campaigns/<campaign_key>/master` - Master view (main dashboard)

**Data Import:**
- `POST /imports/cases` - Upload cases.csv
- `POST /imports/recipients` - Upload recipients.csv
- `POST /imports/templates` - Upload templates.csv

**Generation:**
- `POST /campaigns/<campaign_key>/generate` - Generate variants/prepare online_assign

**Email:**
- `POST /campaigns/<campaign_key>/email-yaml` - Update email settings
- `POST /campaigns/<campaign_key>/send-emails` - Send invitations (Cloudflare/pick_k)
- `POST /campaigns/<campaign_key>/railway/send-emails` - Send invitations (Railway/online_assign)
- `GET /campaigns/<campaign_key>/email-preview` - Preview emails

**Cloud Operations:**
- `POST /campaigns/<campaign_key>/push_to_cloud` - Smart routing (Railway/Cloudflare)
- `POST /campaigns/<campaign_key>/cloud/push` - Push to Cloudflare
- `POST /campaigns/<campaign_key>/railway/push` - Push to Railway
- `POST /campaigns/<campaign_key>/cloud/sync` - Sync submissions from Cloudflare

**Results:**
- `GET /campaigns/<campaign_key>/results` - Aggregated results
- `GET /campaigns/<campaign_key>/submissions` - Individual submissions
- `GET /campaigns/<campaign_key>/recipients` - Recipient management
- `GET /campaigns/<campaign_key>/invitations` - Invitation ledger

**Respondent (Local Testing):**
- `GET /s/<token>` - Survey page
- `POST /s/<token>/submit` - Submit response

### Cloudflare API (Production)

**Respondent Endpoints:**
- `GET /s/<token>` - Survey page (HTML)
- `GET /api/survey/<token>` - Get questionnaire JSON
- `POST /api/submit/<token>` - Submit response (returns 409 on repeat)

**Admin Endpoints (Bearer Auth):**
- `GET /api/admin/ping` - Health check
- `POST /api/admin/upload` - Bulk upload invitations
  - Body: `{campaignKey, invitations: [{email, questionnaireJson, ...}]}`
  - Returns: `{campaignKey, invitations: [{email, token}]}`
- `GET /api/admin/export/<campaignKey>` - Export submissions
  - Returns: `{submissions: [{token, submittedAt, answers: {blockId: value}}]}`

### Railway API (Dynamic Assignment)

Same endpoints as local admin but with PostgreSQL backend:
- `GET /s/<token>` - Assigns questions on first open
- `POST /s/<token>/submit` - Submit with assignment tracking

---

## CSV Contracts

### `cases.csv`

Question bank for case-based studies.

**Required Columns:**
- `case_id` - Unique identifier
- `vignette` - Case description/scenario text
- `prompt` - Question text

**Choices (choose one format):**

Option 1: JSON array in `choices_json` column
```csv
case_id,vignette,prompt,choices_json
case_001,"Patient scenario...","What to do?","[{\"id\":\"A\",\"label\":\"Option A\"},{\"id\":\"B\",\"label\":\"Option B\"}]"
```

Option 2: Separate columns `choice_A`, `choice_B`, etc.
```csv
case_id,vignette,prompt,choice_A,choice_B,choice_C
case_001,"Patient scenario...","What to do?","Option A","Option B","Option C"
```

**Optional Columns:**
- `tags` - Pipe-separated tags (e.g., `cardio|adult|urgent`)

### `recipients.csv`

Participant list.

**Required Columns:**
- `email` - Email address (unique)
- `firstname` - First name
- `lastname` - Last name

**Optional Columns:**
All other columns become `recipientStrata` metadata and can be used for stratification or analysis.

Example:
```csv
email,firstname,lastname,site,specialty,years_experience
alice@example.com,Alice,Smith,Boston,Cardiology,15
bob@example.com,Bob,Jones,NYC,Emergency,8
```

### `templates.csv` (for template_expand)

Template-based question generation.

**Required Columns:**
- `template_id` - Unique identifier
- `vignette_template` - Template text with `{var}` placeholders
- `prompt_template` - Question text with `{var}` placeholders

**Choices:**
Same as cases.csv (JSON or separate columns), labels may include `{var}` placeholders

**Optional Columns:**
- `tags` - Pipe-separated tags
- `rules_yaml` - YAML defining variable selection rules

Example:
```csv
template_id,vignette_template,prompt_template,choice_A,choice_B
tmpl_001,"{age}yo {gender} with {symptom}","Best next step?","Admit","Discharge"
```

### `param_vector.json` (for template_expand)

Variable pools for template expansion.

```json
{
  "pools": {
    "age": [24, 37, 65, 78],
    "gender": ["M", "F"],
    "symptom": ["chest pain", "shortness of breath", "syncope"]
  }
}
```

Templates reference these pools via `rules_yaml`.

---

## Questionnaire JSON Format

### MVP Schema

```json
{
  "title": "Clinical Case Decision Survey",
  "questionnaireVersion": 1,
  "blocks": [
    {
      "type": "vignette",
      "id": "vignette_1",
      "text": "65M with chest pain radiating to left arm..."
    },
    {
      "type": "singleSelect",
      "id": "decision_1",
      "prompt": "What is the best next step?",
      "required": true,
      "choices": [
        {"id": "A", "label": "Aspirin + activate cath lab"},
        {"id": "B", "label": "Order outpatient stress test"},
        {"id": "C", "label": "Discharge with reassurance"},
        {"id": "D", "label": "Give ibuprofen and observe"}
      ]
    }
  ]
}
```

### Block Types

**`vignette`**
- `type`: "vignette"
- `id`: Unique block identifier
- `text`: Scenario/context text

**`singleSelect`**
- `type`: "singleSelect"
- `id`: Unique block identifier
- `prompt`: Question text
- `required`: Boolean (typically true)
- `choices`: Array of `{id, label}`

**`freeText`**
- `type`: "freeText"
- `id`: Unique block identifier
- `prompt`: Question text
- `required`: Boolean
- Single-line text input

### Validation

See `qgen/validation.py` for validation logic:
- Title must be non-empty string
- Version must be integer ≥ 1
- Blocks must be array
- Each block must have valid type and required fields
- Choice IDs must be unique within a question

---

## Deployment Infrastructure

### Environment Variables

**Local Development:**
```bash
export PYTHONPATH="$(pwd)/qgen"
export RESEND_API_KEY='re_...'  # For email sending
export EMAIL_TESTING_OVERRIDE='test@example.com'  # Redirect all emails
```

**Cloud Integration:**
```bash
# Cloudflare (for pick_k_cases)
export CLOUDFLARE_STUDY_BASE_URL='https://study.yourdomain.com'
export CLOUDFLARE_ADMIN_TOKEN='...'  # Matches ADMIN_TOKEN in Cloudflare

# Railway (for online_assign)
export RAILWAY_APP_URL='https://your-app.railway.app'
export RAILWAY_ADMIN_TOKEN='...'  # Matches ADMIN_TOKEN in Railway

# Admin app secret for sessions
export ADMIN_APP_SECRET='random-secret-key'
```

### Cloudflare Deployment

**Requirements:**
- Cloudflare account
- Wrangler CLI installed
- D1 database created

**Setup:**
1. Create D1 database: `wrangler d1 create survey-db`
2. Update `wrangler.toml` with database binding
3. Set secrets: `wrangler secret put ADMIN_TOKEN`
4. Deploy: `wrangler pages deploy`

**See:** [cloudflare/pages/PROVISIONING.md](cloudflare/pages/PROVISIONING.md)

### Railway Deployment

**Requirements:**
- Railway account
- GitHub repository
- PostgreSQL add-on

**Setup:**
1. Connect GitHub repo to Railway
2. Add PostgreSQL database
3. Set environment variables
4. Deploy automatically on push to main

**See:** [docs/RAILWAY_DEPLOYMENT.md](docs/RAILWAY_DEPLOYMENT.md)

---

## Development Setup

### Prerequisites

- Python 3.11+
- Git
- Node.js (for Cloudflare development)

### Local Setup

```bash
# Clone repository
git clone https://github.com/your-org/SurveySays.git
cd SurveySays

# Install Python packages
pip install -e ./admin_app -e ./qgen

# Install development dependencies
pip install pytest black mypy

# Set up environment
export PYTHONPATH="$(pwd)/qgen"
export RESEND_API_KEY='your_test_key'
export EMAIL_TESTING_OVERRIDE='your-email@example.com'

# Start admin app
python3 -m admin_app.admin_app.app
```

### Project Structure

```
SurveySays/
├── qgen/                   # Question generator library
│   ├── qgen/
│   │   ├── __main__.py     # CLI entry point
│   │   ├── generator.py    # Core generation logic
│   │   ├── qpicker/        # Selection strategies
│   │   ├── contracts.py    # TypedDict definitions
│   │   └── validation.py   # JSON validation
│   └── pyproject.toml
├── admin_app/              # Admin web interface
│   ├── admin_app/
│   │   ├── app.py          # Flask app factory
│   │   ├── db.py           # Database functions
│   │   ├── logic.py        # Business logic
│   │   ├── routes/         # HTTP endpoints
│   │   ├── templates/      # Jinja2 templates
│   │   └── utils.py        # Utilities
│   ├── scripts/            # Test scripts
│   └── pyproject.toml
├── cloudflare/             # Cloud deployment
│   └── pages/
│       ├── functions/      # Pages Functions (TypeScript)
│       ├── public/         # Static assets
│       └── schema.sql      # D1 database schema
├── docs/                   # Documentation
├── sample_data/            # Example CSVs
└── README.md
```

---

## Testing

### Running Tests

```bash
# Integration tests
cd admin_app
python scripts/integration_test.py

# UI smoke test
python scripts/ui_smoke_test.py

# Schema conformance
python scripts/test_schema_conformance.py

# Wave workflow test
python scripts/test_wave_workflow.py

# Online assign test
python test_online_assign.py

# Run all tests
python scripts/run_all_tests.py
```

### Test Coverage

**Integration Tests:**
- Campaign creation
- CSV imports
- Variant generation
- Email configuration
- Results export

**Schema Tests:**
- Database table existence
- Column definitions
- Constraints and indices
- Data integrity

**Functional Tests:**
- Online assignment workflow
- Wave generation
- Cloud push/sync
- Email sending

### Manual Testing

1. Start local admin app
2. Import sample data
3. Create test campaign
4. Generate questions
5. Preview respondent view
6. Test submission
7. Verify results

---

## Contributing

### Code Style

- Python: Follow PEP 8
- TypeScript: Prettier + ESLint
- Use type hints in Python
- Document complex logic

### Pull Request Process

1. Create feature branch from `main`
2. Make changes with clear commits
3. Run tests locally
4. Update documentation if needed
5. Submit PR with description
6. Address review feedback

### Release Process

1. Update version numbers
2. Update CHANGELOG
3. Tag release
4. Deploy to staging
5. Test thoroughly
6. Deploy to production

---

## Additional Resources

- **User Guide:** [README.USER.md](README.USER.md)
- **Database Schema:** [docs/database/README.md](docs/database/README.md)
- **Cloudflare Setup:** [cloudflare/pages/PROVISIONING.md](cloudflare/pages/PROVISIONING.md)
- **Railway Setup:** [docs/RAILWAY_DEPLOYMENT.md](docs/RAILWAY_DEPLOYMENT.md)
- **Email Sending:** [docs/RAILWAY_EMAIL_SENDING.md](docs/RAILWAY_EMAIL_SENDING.md)

---

## License

MIT License - see [LICENSE](LICENSE) file for details.
