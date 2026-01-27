# Railway Email Sending Implementation

## Overview

Railway campaigns can now send invitation emails directly to recipients using Resend.com's API, with automatic testing override to prevent accidental real sends.

## Key Features

✅ **Direct Email Sending** - Bypasses Resend templates (which require paid plans)  
✅ **Testing Override** - All emails redirect to `kohane@gmail.com` during testing  
✅ **Personalization** - Client-side variable replacement before sending  
✅ **Rate Limiting** - 0.8 emails/second to respect API limits  
✅ **Error Handling** - Comprehensive error messages with logging  

## Architecture

### Email Flow

```
Local Admin UI
    ├─> User clicks "Send Emails" (Railway Deployment card)
    ├─> Retrieve cloud tokens from local database
    ├─> Get recipient names
    └─> For each recipient:
        ├─> Replace {{{VARIABLES}}} in HTML
        ├─> Call Resend API directly (no template)
        ├─> Override recipient with kohane@gmail.com
        └─> Rate limit: sleep 1.25s between sends
```

### Why Not Templates?

**Resend Templates API requires a paid plan.** Free tier accounts get `403 Forbidden: error code 1010` when attempting to create or update templates.

**Solution:** Send emails directly using Resend's `/emails` endpoint with pre-personalized HTML content.

## Implementation Details

### Files Modified

1. **`admin_app/admin_app/routes/all_routes.py`**
   - Added `railway_send_emails` route
   - Implements direct email sending loop
   - Handles variable replacement client-side

2. **`admin_app/admin_app/resend_client.py`**
   - Added `send_email()` function for direct sending
   - Added `User-Agent` header (required by Resend)
   - Module docstring documenting direct sending

3. **`admin_app/admin_app/templates/master.html`**
   - Added "Send Emails" button in Railway Deployment card
   - Hid original "Send invitation emails" for `online_assign` campaigns
   - Added info box explaining testing override

4. **`restart.py`**
   - Modified to automatically load `.env` file
   - Ensures `RESEND_API_KEY` is available to server process

### Supported Variables

Email HTML can include these variables (triple-brace syntax):

- `{{{SURVEY_LINK}}}` - Personalized Railway survey link
- `{{{CAMPAIGN_TITLE}}}` - Campaign name
- `{{{RECIPIENT_EMAIL}}}` - Recipient's email address
- `{{{RECIPIENT_FIRST_NAME}}}` - Recipient's first name
- `{{{RECIPIENT_LAST_NAME}}}` - Recipient's last name

### Testing Override

**Constant:** `EMAIL_TESTING_OVERRIDE = "kohane@gmail.com"`  
**Location:** `admin_app/admin_app/routes/all_routes.py:2022`

All emails are sent to this address regardless of intended recipient. The intended recipient's email, name, and survey link are still included in the email variables.

**To disable override for production:** Change constant to `None`:
```python
EMAIL_TESTING_OVERRIDE = None  # Production: send to real recipients
```

## API Requirements

### Resend API Key

**Permissions Required:**
- "Full access" (recommended), OR
- "Sending access" (minimum)

**Insufficient permissions cause:**
```
403 Forbidden: error code: 1010
```

Even with verified domains and valid keys, limited-permission keys will fail.

### Domain Verification

Sender email domain must be verified in Resend:

1. Go to https://resend.com/domains
2. Add your domain (e.g., `study.hvp.global`)
3. Add DNS records:
   - TXT for domain verification
   - CNAME for DKIM
   - TXT for SPF (include `amazonses.com`)
4. Wait for verification (~5-15 minutes)

### User-Agent Header

**Required:** All requests to Resend API must include `User-Agent` header.

**Without it:** `403 Forbidden: error code: 1010`

**Implementation:** 
```python
req.add_header("User-Agent", "SurveySays/1.0")
```

This was the final fix that made everything work!

## Usage

### Send Test Emails

1. Create Railway `online_assign` campaign
2. Generate question bank
3. Push to Railway
4. Configure email settings:
   - **From:** `zak@study.hvp.global` (use personal address)
   - **Subject:** Your invitation subject
   - **HTML:** Email body with `{{{SURVEY_LINK}}}` variable
5. Click **"Send Emails"** in Railway Deployment card
6. Check `kohane@gmail.com` for 3 test emails

### Production Sending

1. Set `EMAIL_TESTING_OVERRIDE = None` in `all_routes.py`
2. Restart server: `python restart.py --background`
3. Send emails normally
4. Recipients will receive emails at their actual addresses

## Troubleshooting

### 403 Forbidden: error code: 1010

**Causes:**
1. ❌ API key lacks sending permissions
2. ❌ Sender domain not verified in Resend
3. ❌ Missing User-Agent header (fixed in code)

**Solutions:**
1. Create new API key with "full access" in Resend
2. Verify sender domain in Resend dashboard
3. Ensure code includes `User-Agent` header (already implemented)

### Emails Not Arriving

**Check:**
- `kohane@gmail.com` spam folder
- Success message in UI shows count sent
- Server logs: `tail -50 out/server.log`
- Resend dashboard: https://resend.com/emails

### Rate Limiting

**Current:** 0.8 emails/second (1.25s between sends)  
**Adjust in:** `admin_app/admin_app/routes/all_routes.py:2108`

```python
time.sleep(1.25)  # Adjust this value
```

## Testing Checklist

- [x] Created Railway campaign with `online_assign` strategy
- [x] Generated question bank locally
- [x] Pushed campaign to Railway successfully
- [x] Configured email settings (from, subject, HTML)
- [x] Sender email uses verified domain (`zak@study.hvp.global`)
- [x] Resend API key has "full access" permissions
- [x] Clicked "Send Emails" in Railway Deployment card
- [x] Received 3 test emails at `kohane@gmail.com`
- [x] Each email contains unique Railway survey link
- [x] Survey links work: `https://surveysays-production.up.railway.app/s/{token}`
- [x] Respondent can submit survey successfully

## Future Enhancements

**Potential improvements:**

1. **Batch sending** - Send multiple emails in parallel for speed
2. **Progress indicator** - Real-time progress bar during sending
3. **Send history** - Track which emails were sent when
4. **Retry logic** - Automatically retry failed sends
5. **Unsubscribe handling** - Add unsubscribe links
6. **Delivery tracking** - Webhook integration with Resend
7. **A/B testing** - Multiple email variants

## Related Documentation

- [Railway Deployment Guide](RAILWAY_DEPLOYMENT.md)
- [User Guide](../README.USER.md)
- [Technical Documentation](../README.TECHNICAL.md)

---

**Last Updated:** January 26, 2026  
**Status:** ✅ Production Ready (with testing override enabled)
