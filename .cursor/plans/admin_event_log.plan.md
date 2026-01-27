# Admin event log

## Goal

Track every user event (button clicks) on the Master view with the associated campaign so we know which campaign triggered which action and whether it succeeded or failed.

## Implementation

1. **Schema**: add `event_log` table (`id`, `campaign_id`, `campaign_key`, `event`, `status`, `message`, `created_at`). Add helper functions to insert logs from `admin_app/admin_app/db.py`.
2. **App logging**: wrap buttons/actions (`Generate`, `Prepare`, `Send emails`, `Cloud push`, `Imports`, `Email save`, etc.) in try/except so we log event name + success/failure. `cloud_push` already handles success/failure; reuse helper.
3. **Master view**: add a log panel summarizing recent events (descending by time) with status badges (success/fail) and event names.
4. **Docs/tests**: note the log feature in README and add a test ensuring logs created.

## Files

- `admin_app/admin_app/db.py`
- `admin_app/admin_app/app.py`
- `admin_app/admin_app/templates/master.html`
- `admin_app/scripts/integration_test.py`
- `README.md`

## Acceptance

- Logging helper works for each action.
- Master view shows the latest events with campaign context.
- Integration tests still pass.


