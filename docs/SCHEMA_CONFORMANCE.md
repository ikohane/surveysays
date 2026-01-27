# Schema Conformance Testing

## Overview

This project maintains three separate database schemas:

1. **SQLite schema** (`SCHEMA_SQL` in `admin_app/admin_app/db.py`) - Local development
2. **PostgreSQL schema** (`SCHEMA_SQL_POSTGRES` in `admin_app/admin_app/db.py`) - Railway deployment
3. **Cloudflare D1 schema** (`cloudflare/pages/schema.sql`) - Cloud survey delivery

The schema conformance test (`admin_app/scripts/test_schema_conformance.py`) validates that these schemas are structurally consistent.

## Running the Test

```bash
# Run schema conformance test only
python3 admin_app/scripts/test_schema_conformance.py

# Run all tests (includes schema conformance)
python3 admin_app/scripts/run_all_tests.py
```

## Known Schema Differences

### Expected Differences

#### 1. Admin-Only Tables (SQLite only)

These tables exist in the local admin database but not in Cloudflare D1:

- `app_settings` - Application configuration
- `cases` - Global case pool
- `recipients` - Global recipient pool
- `templates` - Template definitions
- `invitation_variants` - Pre-generated questionnaires (offline strategies)
- `event_log` - Audit log
- `campaign_recipient_exclusions` - Recipient exclusions
- `generation_waves` - Wave tracking for online_assign
- `respondent_assignments` - Question assignments (online_assign)
- `question_items` - Question bank (online_assign)
- `question_stats` - Question statistics (online_assign)
- `cloud_pushes` - Cloud sync tracking
- `cloud_invitation_tokens` - Token mapping
- `cloud_sync_state` - Sync state tracking
- `cloud_uploads` - Upload tracking
- `submission_answers` - Materialized answer data

These tables are intentionally admin-only and should NOT be added to Cloudflare.

#### 2. Cloudflare-Specific Columns

The Cloudflare schema is intentionally minimalist:

**campaigns table**:
- Missing: `seed`, `title`, `questionnaire_version`, `email_*`, `layout_yaml`
- These aren't needed for survey delivery, only for admin operations

**invitations table**:
- Has: `first_name`, `last_name`, `metadata_json`, `questionnaire_version`
- These support the legacy bulk upload format
- SQLite gets these populated during cloud sync

**submissions table**:
- Missing: `email`
- Email is tracked via invitations join

### Current Issues (Need Fixing)

#### 1. SQLite Schema Missing Migrated Columns

**Problem**: The base `SCHEMA_SQL` string doesn't include columns added via migrations.

**Affected tables**:
- `campaigns`: missing `k`, `param_vector_json`, `picker_strategy`

**Why**: These columns are added by `_ensure_campaign_columns()` function, but aren't in the base schema SQL string.

**Fix**: Update `SCHEMA_SQL` to include these columns in the CREATE TABLE statement, then remove them from the migration function.

```sql
CREATE TABLE IF NOT EXISTS campaigns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_key TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  seed INTEGER NOT NULL,
  questionnaire_version INTEGER NOT NULL,
  picker_strategy TEXT NOT NULL DEFAULT 'pick_k_cases',  -- ADD THIS
  k INTEGER NOT NULL DEFAULT 1,                           -- ADD THIS
  param_vector_json TEXT,                                 -- ADD THIS
  created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
  ...
);
```

#### 2. PostgreSQL Has Extra Table

**Problem**: `cloud_invitation_tokens` table exists in PostgreSQL but not in SQLite.

**Fix**: This is likely a legacy table or a bug. Investigate if it's needed, or add it to SQLite schema.

#### 3. Nullable Mismatches

**Problem**: Some columns have different nullable constraints across schemas.

**Affected**:
- `invitations.email`: Cloudflare allows NULL, SQLite requires NOT NULL
- `invitations.questionnaire_json`: Cloudflare requires NOT NULL, SQLite allows NULL

**Fix**: Standardize nullable constraints based on actual requirements.

## CI Integration

The schema conformance test should be run in CI before deploying:

```yaml
# .github/workflows/test.yml
- name: Schema Conformance Test
  run: python3 admin_app/scripts/test_schema_conformance.py
```

## Future Improvements

1. **Automated Schema Migration Generator**
   - Parse differences and generate migration SQL
   - Apply migrations automatically

2. **Schema Versioning**
   - Track schema version in `app_settings`
   - Require migrations for version changes

3. **Type Mapping Validation**
   - Ensure SQLite ↔ PostgreSQL type mappings are correct
   - Catch incompatible type combinations

4. **Index Coverage**
   - Verify all foreign keys have indexes
   - Check query performance implications

## Schema Evolution Guidelines

When adding new columns or tables:

1. **Add to base `SCHEMA_SQL` first** - Don't rely solely on migrations
2. **Update `SCHEMA_SQL_POSTGRES`** - Keep PostgreSQL in sync
3. **Evaluate Cloudflare need** - Only add if required for survey delivery
4. **Run conformance test** - Catch issues before deployment
5. **Document intentional differences** - Update this file

## Troubleshooting

### Test Shows False Positives

If the test reports differences that don't exist:

1. **Check the parser** - The test parses SQL strings, which can be error-prone
2. **Verify actual database** - Use `PRAGMA table_info(table_name)` in SQLite
3. **Check schema SQL** - The string might have formatting issues

### Test Shows Missing Columns That Exist

This likely means:

1. **Columns added via migration** - Update base `SCHEMA_SQL`
2. **Parser bug** - Check test output for parse errors

### Test Passes But Database Differs

This means:

1. **Migration applied** - The test only checks schema SQL strings
2. **Manual ALTER** - Someone modified the database directly
3. **Outdated schema** - Pull latest code

## Related Documentation

- [Database README](database/README.md) - Schema design rationale
- [Technical README](../README.TECHNICAL.md) - Architecture overview
- [Railway Deployment](../docs/RAILWAY_DEPLOYMENT.md) - PostgreSQL deployment
