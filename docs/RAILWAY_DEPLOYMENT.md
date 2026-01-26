# Railway Deployment Guide

Deploy SurveySays Flask app to Railway.app for production-ready `online_assign` campaigns without keeping your laptop running during survey periods.

## Overview

Railway.app hosts your Flask application with PostgreSQL, enabling:
- **Just-in-time question assignment** for `online_assign` campaigns
- **Persistent hosting** - no need to keep your laptop running
- **Auto-deployment from GitHub** - push code changes and Railway redeploys automatically
- **Free tier available** - sufficient for small to medium surveys

## Architecture

```
┌─────────────────────────┐
│   Your Laptop           │
│   ├── Admin UI (Local)  │
│   ├── SQLite Database   │
│   └── Campaign Creation │
└────────┬────────────────┘
         │ Push Campaign
         ↓
┌─────────────────────────┐
│   Railway Cloud         │
│   ├── Flask App         │
│   ├── PostgreSQL DB     │
│   └── Question Bank     │
└────────┬────────────────┘
         │ Survey Links
         ↓
┌─────────────────────────┐
│   Respondents           │
│   Take surveys at       │
│   railway.app/s/token   │
└─────────────────────────┘
```

## Prerequisites

- GitHub account with your SurveySays repository
- Railway.app account (free tier available at https://railway.app)
- Railway connected to your GitHub account

## One-Time Setup

### Step 1: Commit Railway Files

Ensure the following files are in your repository:

- `Procfile` - Tells Railway how to run Flask
- `railway.json` - Service configuration
- `requirements.txt` - Python dependencies

These files should already be in your repo after implementing Railway support.

```bash
# Verify files exist
ls -l Procfile railway.json requirements.txt

# Commit and push to GitHub
git add Procfile railway.json requirements.txt
git commit -m "Add Railway deployment support"
git push origin main
```

### Step 2: Create Railway Project

1. Go to https://railway.app/dashboard
2. Click **"New Project"**
3. Select **"Deploy from GitHub repo"**
4. Choose your **SurveySays** repository
5. Railway will automatically detect the Procfile and begin deployment

### Step 3: Add PostgreSQL Database

1. In your Railway project dashboard, click **"+ New"**
2. Select **"Database"**
3. Choose **"Add PostgreSQL"**
4. Railway automatically:
   - Provisions a PostgreSQL database
   - Sets the `DATABASE_URL` environment variable
   - Links it to your Flask app

### Step 4: Configure Environment Variables

In Railway project dashboard, go to **Variables** tab and add:

| Variable Name | Value | Description |
|--------------|-------|-------------|
| `RESEND_API_KEY` | `re_xxxxx` | Your Resend.com API key for sending emails |
| `RAILWAY_ADMIN_TOKEN` | `<generate>` | Admin token for API authentication (see below) |
| `ADMIN_APP_SECRET` | `<generate>` | Flask session secret (see below) |
| `ADMIN_MODE_DEFAULT` | `local` | Run in local mode (don't sync to Cloudflare) |

**Generate tokens locally:**
```bash
# Generate RAILWAY_ADMIN_TOKEN
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# Generate ADMIN_APP_SECRET
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Copy the output and paste into Railway environment variables.

### Step 5: Get Your Railway URL

1. In Railway project, go to **Settings** → **Domains**
2. Your app URL will be shown (e.g., `surveysays-production.up.railway.app`)
3. Copy this URL - you'll need it for local configuration

### Step 6: Configure Local Admin

On your laptop, set Railway environment variables:

```bash
# Add to ~/.bashrc, ~/.zshrc, or set in your shell session:
export RAILWAY_APP_URL="https://surveysays-production.up.railway.app"
export RAILWAY_ADMIN_TOKEN="<paste_token_from_step4>"
```

Restart your local admin app:
```bash
python3 -m admin_app.admin_app.app
```

## Using Railway for Campaigns

### Create and Push Campaign

1. **Start local admin UI:**
   ```bash
   python3 -m admin_app.admin_app.app
   # Open http://127.0.0.1:5055
   ```

2. **Create campaign locally:**
   - Import `cases.csv` (your questions)
   - Import `recipients.csv` (your survey recipients)
   - Create new campaign with:
     - Strategy: **online_assign**
     - K: Number of questions per recipient (e.g., 5)
   - Click **"Generate question bank"**

3. **Push to Railway:**
   - Go to campaign's **Master** view
   - Find **"Railway Deployment"** card
   - Click **"Push to Railway"**
   - Wait for success message

4. **Send invitations:**
   - Configure email settings
   - Email links will be: `https://your-app.railway.app/s/<token>`
   - Click **"Send emails"**

### How It Works

When you click "Push to Railway":

1. **Question Bank Upload:** All question items from your local database are uploaded to Railway PostgreSQL
2. **Campaign Creation:** Campaign configuration and recipient list are sent to Railway
3. **Token Generation:** Unique tokens are created for each recipient
4. **Just-in-Time Ready:** Railway is now ready to assign questions when recipients open their links

When a respondent clicks their link:

1. Opens `https://your-app.railway.app/s/<token>`
2. Railway Flask app retrieves questions with lowest `assigned_count`
3. Assigns K questions to this respondent
4. Saves assignment and increments counters
5. Displays personalized survey

### Sync Results Back

After respondents submit:

1. In local admin UI, go to campaign **Master** view
2. Find **"Cloud mode sync"** card
3. Click **"Sync from Railway now"**
4. Results are downloaded to your local SQLite database
5. View in **Results** and **Reports** pages

## Automatic Updates

When you push code changes to GitHub:

```bash
# Make changes to code
git add .
git commit -m "Fix bug in question assignment"
git push origin main
```

Railway automatically:
- Detects the push
- Rebuilds your app
- Redeploys within 1-2 minutes
- Zero downtime for respondents

## Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | Auto-set | - | PostgreSQL connection string (Railway auto-sets) |
| `PORT` | Auto-set | 8080 | Server port (Railway auto-sets) |
| `RESEND_API_KEY` | Optional | - | Resend.com API key for email sending |
| `RAILWAY_ADMIN_TOKEN` | Required | - | Admin token for API authentication |
| `RAILWAY_APP_URL` | Required (local) | - | Your Railway app URL (set on laptop) |
| `ADMIN_APP_SECRET` | Required | - | Flask session secret |
| `ADMIN_MODE_DEFAULT` | Optional | `local` | Admin mode (use `local` for Railway) |

## Troubleshooting

### Check Logs

View Railway logs to debug issues:

1. In Railway dashboard, click on your service
2. Go to **"Deployments"** tab
3. Click on latest deployment
4. View **"Logs"** section

### Common Issues

**"Push to Railway" button not showing:**
- Verify `RAILWAY_APP_URL` is set on your laptop
- Verify `RAILWAY_ADMIN_TOKEN` is set on your laptop
- Restart local admin app

**"Invalid admin token" error:**
- Check `RAILWAY_ADMIN_TOKEN` matches on both laptop and Railway
- Tokens are case-sensitive

**Database connection errors:**
- Verify PostgreSQL service is running in Railway dashboard
- Check `DATABASE_URL` is set in Railway variables

**Questions not assigning:**
- Verify question bank was pushed (check logs)
- Ensure campaign strategy is `online_assign`
- Check Railway logs for errors

### Database Access

To query your Railway PostgreSQL database:

1. In Railway dashboard, click on PostgreSQL service
2. Go to **"Connect"** tab
3. Copy connection string
4. Use `psql` or your preferred database tool:
   ```bash
   psql "postgresql://user:pass@host:port/database"
   ```

Example queries:
```sql
-- Check question items
SELECT campaign_id, COUNT(*) FROM question_items GROUP BY campaign_id;

-- Check question stats
SELECT item_id, assigned_count, submitted_count FROM question_stats LIMIT 10;

-- Check invitations
SELECT email, token, opened_at FROM invitations LIMIT 10;
```

## Cost and Limits

**Railway Free Tier:**
- $5 credit per month (unused credit doesn't roll over)
- Sufficient for:
  - ~500 hours of runtime
  - Small PostgreSQL database
  - Reasonable traffic volumes

**Monitoring Usage:**
1. Railway dashboard → Project
2. View **"Usage"** metrics
3. Set up billing alerts if needed

**When to Upgrade:**
- Large surveys (1000+ participants)
- High-traffic periods
- Need for additional resources

## Comparison: Railway vs Cloudflare

| Feature | Railway (online_assign) | Cloudflare (offline) |
|---------|-------------------------|----------------------|
| **Strategy Support** | `online_assign` only | `pick_k_cases`, `template_expand` |
| **Question Assignment** | Just-in-time (balanced) | Pre-generated |
| **Database** | PostgreSQL | Cloudflare D1 |
| **Cost** | $5/month free tier | Free tier available |
| **Setup** | GitHub auto-deploy | Manual wrangler deploy |
| **Best For** | Balanced distribution | Large-scale surveys |

## Support

- **Railway Documentation:** https://docs.railway.app
- **Railway Community:** https://discord.gg/railway
- **SurveySays Issues:** https://github.com/your-repo/issues

---

## Next Steps

- [Back to User Guide](../README.USER.md)
- [Technical Documentation](../README.TECHNICAL.md)
- [Cloudflare Deployment](../cloudflare/pages/PROVISIONING.md)
