# Master Page Reorganization

## Date: January 2, 2026

## Overview
The Master page has been reorganized to create a more logical workflow, with operational cards at the top, administrative cards in the middle, and configuration cards at the bottom.

## New Card Order

### 1. **Header**
- Campaign key, strategy, K value, and mode display
- Quick navigation buttons to Campaign, Results, Recipients, etc.

### 2. **Status** (Position: Top)
- Critical overview metrics: Cases, Recipients, Templates, Variants/Invitations
- First thing users see when entering Master page

### 3. **Variant Generation Waves** ⭐ (Moved from #10 → #3)
- **Rationale**: Generating variants is the FIRST operational task after setting up a campaign
- Shows wave history, generation status, and "Generate Wave X" button
- Positioned immediately after Status to facilitate the primary workflow

### 4. **Invited / opened / assigned / submitted**
- Campaign-level cohort statistics

### 5. **Recent submissions**
- Latest submission activity

### 6. **Cloud mode sync**
- Cloudflare → local SQLite synchronization

### 7. **Cloudflare staging**
- Push variants to Cloudflare and manage tokens

### 8. **Email (Resend) settings** ⭐ (Moved from #13 → #8)
- **Rationale**: Email settings should be configured AFTER staging to Cloudflare
- Positioned after Cloudflare staging card as the next logical step
- Contains actual operational email fields (from, subject, base_url, html template)
- Send invitation emails button included here

### 9. **Data Management (Global)**
- Import cases, recipients, and templates
- Three side-by-side cards for easy access

### 10. **Invites ledger**
- Scrollable view of all invitations and their statuses

### 11. **Event log**
- Recent campaign events and actions

### 12. **Layout (YAML)** ⭐ (Moved from #7 → #12)
- **Rationale**: Advanced configuration, moved to bottom
- YAML-based layout configuration for respondent UI

### 13. **Email Settings (YAML)** ⭐ (Moved from #8 → #13)
- **Rationale**: Advanced configuration, moved to bottom
- YAML-based email template configuration

## Key Changes Summary

1. **Variant Generation Waves moved UP** (from position #10 to #3)
   - Now directly below Status card
   - Makes it clear that generation is the first operational step

2. **Email (Resend) settings moved UP** (from position #13 to #8)
   - Now immediately after Cloudflare staging
   - Creates logical flow: Stage → Configure Email → Send

3. **YAML cards moved DOWN** (from positions #7-8 to #12-13)
   - Both Layout and Email Settings YAML moved to bottom
   - Treats them as advanced/alternative configuration options

## Workflow Benefits

### Primary Workflow (First-time setup):
1. View **Status** → Check data imported
2. Configure **Variant Generation Waves** → Generate Wave 1
3. Review cohort stats and submissions
4. Configure cloud sync and **Cloudflare staging** → Push variants
5. Configure **Email (Resend) settings** → Send invitations
6. Monitor via **Invites ledger** and **Event log**

### Advanced Configuration (Optional):
- Scroll to bottom for **Layout (YAML)** and **Email Settings (YAML)**
- These provide alternative/advanced configuration methods

## Redundancy Note

The user noted that **Email Settings (YAML)** appears redundant with **Email (Resend) settings**. Future enhancement opportunity:
- Implement two-way sync between YAML and Resend settings
- Add ability to save/load named email setting presets
- Allow dropdown selection from previously saved settings

## Test Results

All comprehensive tests passed after reorganization:
- ✅ Integration test passed
- ✅ No linter errors
- ✅ All routes functional

