# Wave-Based Generation Workflow Test

This document describes how to test the new wave-based incremental generation workflow.

## What Changed

1. **Database**: Added `generation_waves` table and `wave_id` column to `invitation_variants`
2. **Generation Logic**: Now supports additive generation (only generates for NEW recipients)
3. **UI Consolidation**: 
   - Home page: Simplified, removed imports
   - Campaign page: Removed generate button, kept only picker config
   - Master page: Now the hub for all operations (imports, generation, email, monitoring)

## Test Workflow

### 1. Start the Admin Server

```bash
cd admin_app
python -m admin_app.app
```

The server should start on `http://127.0.0.1:5055`

### 2. Create a Campaign

1. Go to Home page
2. Fill in campaign creation form:
   - campaign_key: `test_waves`
   - title: `Test Wave Generation`
   - seed: `42`
   - questionnaire_version: `1`
3. Click "Create Campaign"

### 3. Configure Picker Strategy

1. You'll be redirected to the Campaign page
2. In the "Picker configuration" section, set:
   - picker_strategy: `pick_k_cases`
   - k: `2`
3. Click "Save picker settings"
4. Click "Go to Master Page"

### 4. Import Initial Data

On the Master page, in the "Data Management" section:

1. **Import Cases**:
   - Choose `sample_data/cases.csv`
   - Keep "Replace existing" checked
   - Click "Import Cases"

2. **Import Initial Recipients**:
   - Choose `sample_data/smallrecipients.csv` (has 3 recipients)
   - Uncheck "Replace existing" (for incremental additions later)
   - Click "Import Recipients"

### 5. Generate Wave 1

In the "Variant Generation Waves" section:

1. You should see: "0 of 3 recipients have variants · 3 pending"
2. Click "Generate Wave 1 for 3 new recipients"
3. Wait for generation to complete
4. You should see:
   - Success message: "Wave 1: Generated X variants for 3 new recipients"
   - Wave history table showing Wave 1
   - Status: "3 of 3 recipients have variants"

### 6. Import Additional Recipients

Back in the "Data Management" section:

1. **Import More Recipients**:
   - Choose `sample_data/recipients.csv` (has more recipients)
   - **UNCHECK** "Replace existing" (this adds to existing)
   - Click "Import Recipients"
2. Note the new total recipient count

### 7. Generate Wave 2

In the "Variant Generation Waves" section:

1. You should now see: "3 of N recipients have variants · M pending" (where N > 3)
2. Click "Generate Wave 2 for M new recipients"
3. Wait for generation to complete
4. You should see:
   - Success message: "Wave 2: Generated X variants for M new recipients"
   - Wave history table showing both Wave 1 and Wave 2
   - Status: "N of N recipients have variants"

### 8. Verify Wave Tracking

Check the wave history table:
- Wave 1: Should show 3 recipients processed
- Wave 2: Should show M recipients processed
- Each wave should have strategy `pick_k_cases` and k=2

### 9. Test "All Recipients Have Variants" State

Try clicking the generate button again:
- It should be disabled
- Button text: "All recipients have variants"

## Expected Behavior

### Additive Generation
- ✅ Wave 1 generates for ALL recipients in the pool
- ✅ Wave 2 generates ONLY for NEW recipients (not in Wave 1)
- ✅ Existing variants from Wave 1 are preserved
- ✅ Each wave is tracked with metadata (recipients processed, variants created, strategy, k, seed)

### UI Flow
- ✅ Home page: Simple campaign creation, no imports
- ✅ Campaign page: Configure picker strategy, link to Master
- ✅ Master page: All operations (import, generate, email, monitor)

### Wave Tracking
- ✅ Each generation creates a new wave record
- ✅ Wave numbers increment (1, 2, 3, ...)
- ✅ Variants are linked to their wave via `wave_id`
- ✅ Wave history is displayed in a table

## Troubleshooting

### "No recipients imported yet"
- Go to Master page → Data Management → Import Recipients

### "All recipients already have variants"
- This is correct if you haven't imported new recipients
- Import more recipients to enable Wave 2

### Generate button redirects to wrong page
- All generation now redirects to Master page
- Error messages tell you to use Master page

## Database Verification

If you want to verify the database directly:

```bash
sqlite3 out/local_admin.sqlite3
```

```sql
-- Check generation waves
SELECT * FROM generation_waves ORDER BY wave_number;

-- Check variants with wave_id
SELECT wave_id, COUNT(*) as n 
FROM invitation_variants 
GROUP BY wave_id 
ORDER BY wave_id;

-- Check recipients with variants
SELECT COUNT(DISTINCT email) as recipients_with_variants
FROM invitation_variants
WHERE campaign_id = (SELECT id FROM campaigns WHERE campaign_key = 'test_waves');
```

