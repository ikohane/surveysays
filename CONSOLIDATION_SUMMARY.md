# Campaign Workflow Consolidation - Implementation Summary

## Overview

Successfully consolidated redundant import and generation functions from three locations (Home, Campaign, Master pages) into a single operational hub (Master page), and implemented wave-based generation tracking to support incremental recipient additions.

## Changes Made

### 1. Database Schema (`admin_app/admin_app/db.py`)

#### New Table: `generation_waves`
```sql
CREATE TABLE generation_waves (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id INTEGER NOT NULL,
  wave_number INTEGER NOT NULL,
  picker_strategy TEXT NOT NULL,
  k INTEGER NOT NULL,
  seed INTEGER NOT NULL,
  recipients_processed INTEGER NOT NULL,
  variants_created INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
  UNIQUE (campaign_id, wave_number),
  FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
```

#### Schema Modification
- Added `wave_id INTEGER` column to `invitation_variants` table
- Added index on `wave_id` for performance

#### New Database Functions
- `insert_generation_wave()` - Record a new generation wave
- `get_next_wave_number()` - Get next wave number for a campaign
- `list_generation_waves()` - List all waves for a campaign
- `get_recipients_with_variants()` - Get set of emails that already have variants
- Updated `insert_variants()` to accept and store `wave_id`

### 2. Generation Logic (`admin_app/admin_app/routes/all_routes.py`)

#### Additive Wave Generation
The `/campaigns/<campaign_key>/generate` route now:
1. Determines the next wave number
2. Queries which recipients already have variants
3. Filters to only NEW recipients (additive, not destructive)
4. Generates variants only for new recipients
5. Records the wave with metadata
6. Links variants to the wave via `wave_id`

#### Strategy Support
Works for all three picker strategies:
- **pick_k_cases**: Generates from cases.csv for new recipients
- **template_expand**: Generates from templates.csv + param_vector for new recipients
- **online_assign**: Creates invitations for new recipients, builds question bank once (Wave 1)

#### Redirect Updates
- All import routes now accept optional `campaign_key` parameter
- Redirects back to `master_view` when called from Master page
- Error messages updated to reference Master page

### 3. Home Page Simplification (`admin_app/admin_app/templates/home.html`)

#### Removed
- All import forms (cases, recipients, templates)
- Picker strategy and k fields from campaign creation

#### Kept
- Admin mode settings
- Campaign creation form (simplified to: key, title, seed, version)
- Campaign list with links
- Read-only global data counts

#### Added
- Guidance text directing users to Master page for imports
- "Configure" and "Master" links in campaign table

#### Route Changes
- `campaigns_upsert` no longer sets picker_strategy or k
- These are now set via `update_campaign_settings` on Campaign page

### 4. Campaign Page Simplification (`admin_app/admin_app/templates/campaign.html`)

#### Removed
- "Generate per-recipient variants" card with Generate/Prepare button
- Export buttons (moved to Master)

#### Kept
- Campaign metadata display
- Picker configuration section (strategy, k, param_vector upload)
- Status overview (counts)
- Quick links to other pages

#### Added
- "Next Steps" card with prominent "Go to Master Page" button
- Clear guidance on what to do next

### 5. Master Page Enhancement (`admin_app/admin_app/templates/master.html`)

#### New Section: Data Management (Global)
Moved from disclosure triangle to main section:
- Import Cases form (with replace option)
- Import Recipients form (with replace option)
- Import Templates form
- All forms include hidden `campaign_key` field for proper redirects

#### New Section: Variant Generation Waves
Replaces old "Actions" disclosure:
- **Status Display**: Shows X of Y recipients have variants, Z pending
- **Wave History Table**: Lists all waves with:
  - Wave number
  - Created timestamp
  - Recipients processed
  - Variants created
  - Strategy used
  - K value
- **Generate Button**: 
  - Shows "Generate Wave N for X new recipients"
  - Disabled when all recipients have variants
  - Button text updates dynamically
- **Export Buttons**: Download JSON exports

#### Removed
- "Recommended first-time flow" card (redundant)
- "Advanced / Re-run" disclosure triangle (promoted to main sections)

#### Updated
- `master_view` route now passes:
  - `generation_waves` - list of all waves
  - `recipients_with_variants` - count of recipients with variants
  - `pending_recipients` - count of recipients without variants

### 6. Route Updates

#### Import Routes
All three import routes (`/imports/cases`, `/imports/recipients`, `/imports/templates`) now:
- Accept optional `campaign_key` form parameter
- Redirect to `master_view` if `campaign_key` is provided
- Fall back to `request.referrer` or `home` if not

#### Generation Route
`/campaigns/<campaign_key>/generate` now:
- Redirects to `master_view` (not `campaign_detail`)
- Flash messages reference Master page
- Implements wave-based additive generation

## Workflow Changes

### Before
```
Home → Import data → Create campaign → Campaign page → Generate → Master → Email
  ↓                                         ↓
  ↓                                    (or Master → Generate)
  ↓
  Master → Import data → Generate
```
**Problem**: Redundant import/generate in 3 places, confusing workflow

### After
```
Home → Create campaign → Campaign page → Configure picker → Master → Everything
                                                                ↓
                                                    Import → Generate waves → Email → Monitor
```
**Solution**: Single operational hub (Master), clear linear flow

## Key Features

### 1. Incremental Recipient Addition
- Import initial recipients → Generate Wave 1
- Import more recipients → Generate Wave 2
- Each wave only processes NEW recipients
- Previous waves' variants are preserved

### 2. Wave Tracking
- Every generation creates a wave record
- Wave metadata: number, timestamp, recipients, variants, strategy, k, seed
- Variants linked to waves via `wave_id`
- Full history visible in UI

### 3. Clear UI Guidance
- Home: "Go to Master page for imports"
- Campaign: "Go to Master page to generate"
- Master: All operations in one place
- Error messages point to correct location

### 4. Backward Compatibility
- Existing campaigns work (wave_id can be NULL)
- First generation after upgrade becomes "Wave 1"
- No data loss or migration issues

## Testing

See `WAVE_WORKFLOW_TEST.md` for detailed manual testing instructions.

### Quick Test
1. Create campaign on Home
2. Configure picker on Campaign page
3. Go to Master page
4. Import cases + 3 recipients → Generate Wave 1
5. Import 5 more recipients → Generate Wave 2
6. Verify wave history shows both waves
7. Verify all 8 recipients have variants

## Files Modified

1. `admin_app/admin_app/db.py` - Schema + wave functions
2. `admin_app/admin_app/routes/all_routes.py` - Generation logic + redirects
3. `admin_app/admin_app/templates/home.html` - Simplified
4. `admin_app/admin_app/templates/campaign.html` - Removed generate
5. `admin_app/admin_app/templates/master.html` - Enhanced with waves

## Migration Notes

### For Existing Databases
- `wave_id` will be NULL for pre-existing variants (acceptable)
- No `generation_waves` records for past generations (acceptable)
- First new generation after upgrade will be "Wave 1"
- All existing functionality continues to work

### For Users
- **Breaking Change**: Import forms removed from Home page
- **Breaking Change**: Generate button removed from Campaign page
- **New Workflow**: All operations now done via Master page
- **Benefit**: Clearer workflow, wave tracking, incremental additions

## Benefits

1. **Single Source of Truth**: Master page is the operational hub
2. **Incremental Workflow**: Add recipients anytime, generate new waves
3. **Full Traceability**: Every generation tracked with metadata
4. **Clearer UX**: Each page has a clear purpose
5. **Additive Generation**: Never lose previous work
6. **Wave History**: See exactly when and how variants were generated

## Future Enhancements

Possible additions:
- Wave comparison (diff between waves)
- Wave rollback (revert to previous wave)
- Wave export (download specific wave's variants)
- Per-wave email sending (send only to Wave 2 recipients)

