# Basic/Expert Mode Implementation Summary

## Overview

Successfully implemented a dual-mode interface system allowing users to toggle between:
- **Basic Mode**: Simplified UI for non-technical users
- **Expert Mode**: Full-featured interface with technical details

## Implementation Details

### Core Features Implemented

#### 1. Mode Toggle System ✅
- **Location**: Header of every page
- **Persistence**: Flask session-based, survives page navigation
- **Default**: Basic mode for new users
- **Toggle Route**: `/toggle_ui_mode` (POST)

#### 2. Home Page Conditionals ✅
**Basic Mode:**
- Title: "Surveys" (not "Campaigns")
- Hidden: Admin mode selector, Global Data card
- Simplified form: "Survey Name", "Survey Type", "Questions per person"
- Friendly picker options: "Fixed Questions", "Balanced Distribution", "Template-Based"
- Simplified table: Survey Name, Title, Type, Actions

**Expert Mode:**
- Full technical interface unchanged
- Shows all fields: seed, questionnaire_version, etc.
- Technical picker names: pick_k_cases, online_assign, template_expand

#### 3. Master Page Simplification ✅
**Basic Mode Shows:**
- Status overview (Cases, Recipients, Invited stats)
- Quick Workflow Guide (5-step checklist)
- Email (Resend) settings (form fields)
- Data Management (CSV uploads)
- Recipient Status (simplified ledger with checkmarks)
- Railway Deployment (for online_assign only)

**Basic Mode Hides:**
- Variant Generation Waves
- Invited/opened/assigned/submitted card
- Recent submissions
- Cloud mode sync (Cloudflare)
- Cloudflare staging
- Event log
- Layout YAML
- Email Settings YAML

**Navigation:**
- Basic: Only Results + Recipients buttons
- Expert: All navigation buttons (Campaign, Invitations, Online stats, Reports, etc.)

#### 4. Recipient Status Ledger ✅
**Basic Mode:**
- Columns: Email, First Name, Last Name, Sent, Opened, Submitted
- Values: Checkmarks (✓) or dashes (—)
- Hidden: Tokens, Links, Timestamps, Assigned column

**Expert Mode:**
- Full technical details with timestamps
- Token column with direct survey links
- Assigned column showing questionnaire_hash

#### 5. Smart Cloud Routing ✅
- **Route**: `/campaigns/<key>/push_to_cloud`
- **Logic**: Automatically routes to Railway for `online_assign`, Cloudflare otherwise
- **Used by**: Basic mode's unified "Push to Cloud" button

#### 6. Cloudflare Sync Fix ✅
- **Issue**: Railway campaigns (`online_assign`) were triggering 403 errors trying to sync from Cloudflare
- **Fix**: Skip Cloudflare sync for `online_assign` campaigns (they use Railway)
- **Template**: Hide Cloudflare cards for `online_assign` campaigns

#### 7. Email Configuration ✅
- **Dual Input**: Accepts both YAML (Expert) and individual form fields (Basic)
- **Auto Base URL**: Intelligently selects Railway or Cloudflare URL
- **Backend**: `update_email_yaml` route handles both input types

#### 8. Database Queries ✅
- **Ledger**: Added firstname/lastname extraction from strata_json
- **SQLite**: Uses `json_extract(r.strata_json, '$.firstname')`
- **Both platforms**: Works for local invitations and cloud tokens

#### 9. Error Formatting ✅
- **Function**: `format_error_for_ui_mode()` in logic.py
- **Basic Mode**: User-friendly, actionable messages
- **Expert Mode**: Full technical error details
- **Pattern Matching**: Maps common errors to friendly versions

## Testing Results

### ✅ UI Smoke Test
- All 10 campaign pages load successfully (200 OK)
- No template errors
- Navigation working correctly

### ✅ Manual Browser Testing
- ✓ Mode toggle works and persists
- ✓ Basic mode hides expert cards
- ✓ Expert mode shows all cards
- ✓ Navigation buttons update correctly
- ✓ Cloudflare sync error eliminated for Railway campaigns
- ✓ Recipient Status ledger displays correctly

### ⚠️ Integration Tests
- Modified to support both modes
- Some pre-existing test failures unrelated to UI changes
- Tests force Expert mode for consistency

### ⚠️ Schema Conformance
- Shows expected differences between SQLite and Cloudflare D1
- Not blocking - differences are intentional design choices

## Files Modified

| File | Changes |
|------|---------|
| `routes/all_routes.py` | Added toggle route, get_ui_mode(), ui_mode to contexts, skip Cloudflare sync for online_assign, smart push_to_cloud routing, updated update_email_yaml |
| `templates/base.html` | Added mode toggle button in header |
| `templates/home.html` | Conditional rendering for Basic vs Expert, simplified forms and labels |
| `templates/campaign.html` | Hide picker configuration card in Basic mode, simplified Next Steps |
| `templates/master.html` | Wrapped expert cards in conditionals, added workflow guide, simplified navigation, updated ledger table |
| `db.py` | Added firstname/lastname to ledger queries using json_extract |
| `logic.py` | Added format_error_for_ui_mode() function |
| `scripts/integration_test.py` | Updated to handle both modes |

## Git Commits (feature/basic-expert-mode branch)

1. `002cd41` - Add Basic/Expert mode toggle foundation
2. `01fae2b` - Add Basic mode workflow guide and update integration tests  
3. `c72338f` - Fix: Prevent Cloudflare sync 403 errors for Railway campaigns
4. `980ab12` - Hide expert-only cards in Basic mode
5. `eb1a8af` - Simplify Invites Ledger for Basic mode
6. `74149e7` - Add format_error_for_ui_mode function for friendly errors
7. `599f4b1` - Add comprehensive implementation summary
8. `6e459af` - Clean up temporary development scripts
9. `420b41e` - Add comprehensive testing results documentation
10. `1c6ff29` - Add Basic/Expert mode support to Campaign configuration page

## Usage

### For Non-Technical Users (Basic Mode)
1. Default mode when first visiting the app
2. Create a survey with friendly labels
3. Follow the 5-step workflow guide
4. Upload data, configure email, push to cloud, send
5. Track recipient status with simple checkmarks

### For Technical Users (Expert Mode)
1. Click "Switch to Expert Mode" in header
2. Access all technical features
3. View event logs, raw YAML, detailed timestamps
4. Debug with full error messages
5. Access all navigation pages

## Future Enhancements

- [ ] Integrate format_error_for_ui_mode() into all flash() calls
- [ ] Auto-wave creation for follow-up emails in Basic mode
- [ ] Per-user mode preference (requires authentication)
- [ ] Guided tutorials specific to each mode
- [ ] Analytics on mode usage patterns

## Known Limitations

- Auto-wave feature not yet implemented (users manually click "Generate Wave")
- Error formatting function defined but not yet integrated into all routes
- Some integration tests have pre-existing failures unrelated to UI changes
