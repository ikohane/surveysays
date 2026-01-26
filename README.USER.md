# SurveySays - Survey Creation & Delivery System

**User Guide for Survey Administrators**

## What Can You Do With This System?

SurveySays helps you create, customize, and send surveys to recipients. You can:

✅ **Create surveys** from question banks (CSV files)  
✅ **Personalize each survey** - different recipients get different question combinations  
✅ **Send invitation emails** automatically with personalized links  
✅ **Track responses** in real-time  
✅ **Manage recipients** - exclude/restore individuals from campaigns  
✅ **Export results** for analysis  
✅ **Run multiple campaigns** simultaneously with different question sets

### Two Delivery Modes

1. **Local Mode** (Testing)
   - Surveys hosted on your computer
   - Good for testing and small studies with trusted recipients
   - Recipients click links that point to your machine

2. **Cloud Mode** (Production)
   - Surveys hosted on Cloudflare (professional hosting)
   - Good for large-scale studies and public recipients
   - Recipients click links that point to your domain (e.g., https://study.yourdomain.com)

---

## Prerequisites

Before you start, you need:

### Required Software
- **Python 3.11 or newer** installed on your computer
  - Check: Open Terminal/Command Prompt and type `python3 --version`
  - Install from: https://www.python.org/downloads/

### Required Files
- **Questions** file (`cases.csv`) - your question bank
- **Recipients** file (`recipients.csv`) - list of people to survey

### Optional (For Email Sending)
- **Resend.com account** (free tier available)
  - Sign up at https://resend.com
  - You'll need an API key

### Optional (For Cloud Hosting)
- **Cloudflare account** (free tier available)
  - Only needed if you want professional hosting
  - See "Cloud Mode Setup" section below

---

## Installation & Setup

### Step 1: Install the Application

```bash
# Navigate to the SurveySays folder
cd /path/to/SurveySays

# Install required Python packages
pip install -e ./admin_app
pip install -e ./qgen
```

### Step 2: Configure Email Sending (Optional but Recommended)

To send invitation emails, you need a Resend.com API key:

1. Sign up at https://resend.com (free for up to 3,000 emails/month)
2. Get your API key from the dashboard
3. Set the environment variable:

**On Mac/Linux:**
```bash
export RESEND_API_KEY='re_your_api_key_here'
```

**On Windows:**
```cmd
set RESEND_API_KEY=re_your_api_key_here
```

**Verify your sending domain:**
- In Resend dashboard, add and verify your domain (e.g., yourdomain.com)
- Use a "from" email like: `surveys@yourdomain.com`

### Step 3: Start the Admin Application

```bash
# Make sure you're in the SurveySays folder
cd /path/to/SurveySays

# Set Python path
export PYTHONPATH="$(pwd)/qgen"

# Start the admin app
python3 -m admin_app.admin_app.app
```

The application will start at: **http://127.0.0.1:5055**

Open this URL in your web browser.

---

## Creating Your First Survey

### Step 1: Prepare Your Data Files

#### **Recipients File** (`recipients.csv`)

Required columns:
- `email` - recipient's email address
- `firstname` - recipient's first name  
- `lastname` - recipient's last name

Example:
```csv
email,firstname,lastname,institution,role
alice@example.com,Alice,Smith,MIT,Professor
bob@example.com,Bob,Jones,Harvard,Researcher
```

Any extra columns (like `institution`, `role`) will be saved as metadata.

#### **Questions File** (`cases.csv`)

Required columns:
- `case_id` - unique identifier (e.g., case_001)
- `vignette` - the scenario or context
- `prompt` - the question being asked
- `choice_A` - first answer option
- `choice_B` - second answer option
- (optional) `choice_C`, `choice_D`, etc.

Example:
```csv
case_id,vignette,prompt,choice_A,choice_B
case_001,"A 45-year-old patient presents with chest pain...","What is your diagnosis?","Myocardial infarction","Angina pectoris"
case_002,"A 30-year-old reports severe headaches...","Recommended treatment?","MRI scan","CT scan"
```

### Step 2: Import Your Data

1. Open **http://127.0.0.1:5055** in your browser
2. Click **"Import Cases"**
   - Upload your `cases.csv` file
   - You'll see a confirmation with the count
3. Click **"Import Recipients"**
   - Upload your `recipients.csv` file
   - You'll see a confirmation with the count

### Step 3: Create a Campaign

1. On the home page, click **"Create New Campaign"**
2. Fill in:
   - **Campaign Key**: Short identifier (e.g., `study_2025_01`)
   - **Title**: Display name (e.g., "Clinical Case Study January 2025")
   - **Seed**: Random number for reproducibility (e.g., `12345`)
   - **Version**: Usually `1` for first campaign
3. Click **"Create Campaign"**

### Step 4: Configure Campaign Settings

Click on your campaign name to open the campaign page.

#### **Choose Your Strategy:**

**Pick K Cases** (Most Common)
- Each recipient gets K randomly selected questions
- Use this for: General surveys where order doesn't matter
- Configuration: Set **K** = number of questions per recipient (e.g., 5)

**Online Assign** (Advanced - Requires Railway)
- Questions assigned when recipient opens their link
- Use this for: Balancing question distribution, adaptive testing
- Configuration: Set **K** = number of questions per recipient
- **Deployment**: Requires Railway.app for production (see [Railway Deployment Guide](docs/RAILWAY_DEPLOYMENT.md))

**Template Expand** (Expert)
- Questions generated from templates with variables
- Use this for: Complex parameterized questions
- Requires: Additional `templates.csv` and `param_vector.json` files

### Step 5: Generate Surveys

1. On the campaign page, click **"Generate Variants"**
2. The system creates personalized surveys for each recipient
3. You'll see a summary:
   - Number of recipients processed
   - Number of unique questionnaires created
   - Wave number (for tracking multiple generations)

### Step 6: Preview Your Surveys

1. Click **"Preview"** to see what a survey looks like
2. Click **"Stats"** to see distribution of questions

### Step 7: Configure Email Content

1. Click **"Master View"** (navigation bar)
2. Scroll to **"Email Configuration"**
3. Edit the email template:
   - **From Email**: Must match your verified Resend domain (e.g., `surveys@yourdomain.com`)
   - **Subject**: Email subject line (e.g., "You're invited to participate")
   - **Base URL**: 
     - Local Mode: `http://127.0.0.1:5055`
     - Cloud Mode: Your Cloudflare URL (e.g., `https://study.yourdomain.com`)
   - **HTML**: Email body with variables:
     - `{{{RECIPIENT_FIRST_NAME}}}` - recipient's first name
     - `{{{RECIPIENT_LAST_NAME}}}` - recipient's last name
     - `{{{SURVEY_LINK}}}` - personalized survey link
     - `{{{CAMPAIGN_TITLE}}}` - your campaign title

4. Click **"Save Email Settings"**

### Step 8: Preview Emails (Recommended)

1. Click **"Email Preview"** button
2. Review how emails will look for each recipient
3. Check that all variables are substituted correctly
4. Verify links work

### Step 9: Send Invitation Emails

⚠️ **Make sure you've configured Resend API key** (see Installation Step 2)

1. On the Master View page, click **"Send Invitation Emails"**
2. The system will:
   - Create/update your email template in Resend
   - Send personalized emails to all recipients
   - Show progress (approximately 1 email per second)
3. Check for any errors in the confirmation message

---

## Managing Recipients

### Excluding Recipients

If someone shouldn't receive the survey (e.g., declined participation):

1. Go to **Campaign → Recipients** (navigation bar)
2. Find the recipient in the "Pending Recipients" list
3. Click **"Exclude"** next to their email
4. They won't receive emails for this campaign

### Restoring Recipients

To re-add an excluded recipient:

1. Go to **Campaign → Recipients**
2. Find them in the "Excluded Recipients" list
3. Click **"Restore"**

### Adding New Recipients (Waves)

To add more recipients after initial generation:

1. Edit your `recipients.csv` to include new people
2. Import the updated file (**"Import Recipients"** on home page)
3. On your campaign page, click **"Generate Variants"** again
4. The system creates surveys ONLY for new recipients (existing ones unchanged)
5. Send emails to the new batch

---

## Viewing Results

### Quick Overview

1. Go to **Campaign → Master View**
2. See real-time counts:
   - Invitations sent
   - Surveys opened
   - Surveys submitted

### Detailed Results

1. Go to **Campaign → Results**
2. View:
   - **Single-choice questions**: Bar charts showing response distribution
   - **Free-text answers**: List of written responses
   - Overall completion rate

### Individual Submissions

1. Go to **Campaign → Results**
2. Click **"View Individual Submissions"**
3. See each respondent's complete answers

### Export Data

On the Master View page:
- **Download CSV**: Export all submission data
- **Download Tokens**: Get list of survey links

---

## Cloud Mode Setup (Optional - For Production)

Cloud Mode hosts surveys on Cloudflare for professional delivery.

### Prerequisites

1. **Cloudflare account** (free tier available)
2. **Custom domain** (e.g., yourdomain.com)
3. Your domain's DNS managed by Cloudflare

### One-Time Setup

1. Deploy to Cloudflare Pages (see `cloudflare/pages/PROVISIONING.md`)
2. Get your:
   - **Cloudflare URL**: e.g., `https://study.yourdomain.com`
   - **Admin Token**: Secret key for API access

3. Set environment variables on your computer:

**On Mac/Linux:**
```bash
export CLOUDFLARE_STUDY_BASE_URL='https://study.yourdomain.com'
export CLOUDFLARE_ADMIN_TOKEN='your_admin_token_here'
```

**On Windows:**
```cmd
set CLOUDFLARE_STUDY_BASE_URL=https://study.yourdomain.com
set CLOUDFLARE_ADMIN_TOKEN=your_admin_token_here
```

### Using Cloud Mode

1. **Switch to Cloud Mode**:
   - On the home page, select **"Cloud"** mode and click **"Update Mode"**

2. **Push to Cloudflare**:
   - On campaign Master View, click **"Push to Cloudflare"**
   - This uploads your surveys and generates secure tokens
   - Survey links will be: `https://study.yourdomain.com/s/SECURE_TOKEN`

3. **Update Email Base URL**:
   - In Email Configuration, set **Base URL** to your Cloudflare URL
   - e.g., `https://study.yourdomain.com`

4. **Send Emails** as normal
   - Emails now contain Cloudflare links
   - Respondents access surveys on your domain

5. **Sync Results**:
   - Results automatically sync from Cloudflare to your local database
   - View results the same way (Results page)
   - Manual sync: Click **"Sync from Cloudflare Now"** on Master View

### Railway Deployment (for online_assign)

**When to use Railway:**
- You're using the `online_assign` strategy
- You want just-in-time question assignment
- You need perfect question balance across respondents

**Quick Setup:**

1. **Deploy to Railway** (see [Railway Deployment Guide](docs/RAILWAY_DEPLOYMENT.md)):
   - Connect your GitHub account to Railway
   - Create new project from your SurveySays repo
   - Add PostgreSQL database
   - Railway auto-deploys from GitHub

2. **Set Environment Variables** (on your computer):

**On Mac/Linux:**
```bash
export RAILWAY_APP_URL='https://your-app.railway.app'
export RAILWAY_ADMIN_TOKEN='your_railway_token_here'
```

**On Windows:**
```cmd
set RAILWAY_APP_URL=https://your-app.railway.app
set RAILWAY_ADMIN_TOKEN=your_railway_token_here
```

3. **Push Campaign to Railway**:
   - Create campaign with `online_assign` strategy
   - Click **"Generate question bank"**
   - On Master View, click **"Push to Railway"**
   - This uploads question bank and creates invitations

4. **Send Emails**:
   - In Email Configuration, set **Base URL** to Railway URL
   - e.g., `https://your-app.railway.app`
   - Send emails as normal

5. **Respondents Access**:
   - Links: `https://your-app.railway.app/s/SECURE_TOKEN`
   - Questions assigned when they open the link
   - Perfect balance: least-assigned questions are chosen

**Key Differences: Railway vs Cloudflare**

| Feature | Railway (online_assign) | Cloudflare (offline) |
|---------|------------------------|----------------------|
| Strategy | `online_assign` only | `pick_k_cases`, `template_expand` |
| Assignment | Just-in-time | Pre-generated |
| Database | PostgreSQL | Cloudflare D1 |
| Deployment | GitHub auto-deploy | Manual wrangler |
| Best For | Balanced distribution | Large-scale surveys |

**Full Guide:** [docs/RAILWAY_DEPLOYMENT.md](docs/RAILWAY_DEPLOYMENT.md)

---

## Configuration Reference

### Environment Variables

Set these BEFORE starting the admin app:

| Variable | Required? | Purpose | Example |
|----------|-----------|---------|---------|
| `RESEND_API_KEY` | For email | Resend.com API key | `re_abc123...` |
| `CLOUDFLARE_STUDY_BASE_URL` | For Cloudflare | Your survey domain | `https://study.yourdomain.com` |
| `CLOUDFLARE_ADMIN_TOKEN` | For Cloudflare | API authentication | `long_random_string` |
| `RAILWAY_APP_URL` | For Railway | Your Railway app URL | `https://your-app.railway.app` |
| `RAILWAY_ADMIN_TOKEN` | For Railway | Railway API auth | `long_random_string` |
| `ADMIN_MODE_DEFAULT` | Optional | Default mode on startup | `local` or `cloud` |
| `PORT` | Optional | Admin app port | `5055` (default) |
| `ADMIN_APP_DB` | Optional | Database location | `./out/local_admin.sqlite3` (default) |

### Campaign Settings

**Picker Strategy:**
- `pick_k_cases`: Random selection from question bank
- `online_assign`: Assignment when link is opened
- `template_expand`: Template-based generation

**K Value:**
- Number of questions each recipient receives
- Common values: 3-10 questions

**Seed:**
- Controls randomization (same seed = same results)
- Use different seeds for different campaigns

---

## Troubleshooting

### "RESEND_API_KEY is not set"
- You forgot to set the environment variable
- Set it and restart the admin app

### "No recipients imported yet"
- Import `recipients.csv` before generating variants

### "No cases imported yet"
- Import `cases.csv` before generating variants

### Email sending fails
- Verify your Resend API key is correct
- Check that "from" email matches verified domain in Resend
- Ensure rate limits aren't exceeded (max ~1/second)

### Cloud push fails with "Missing env vars"
- Set `CLOUDFLARE_STUDY_BASE_URL` and `CLOUDFLARE_ADMIN_TOKEN`
- Restart admin app after setting variables

### "CERTIFICATE_VERIFY_FAILED" error
- Install/update certifi: `pip install --upgrade certifi`

### Recipients not receiving emails
- Check they're not in the "Excluded Recipients" list
- Verify email addresses are correct in `recipients.csv`
- Check Resend dashboard for delivery status

### Admin app won't start
- Check Python version: `python3 --version` (must be 3.11+)
- Reinstall dependencies: `pip install -e ./admin_app -e ./qgen`

---

## Quick Reference: Complete Workflow

1. ✅ Install Python 3.11+
2. ✅ Install application: `pip install -e ./admin_app -e ./qgen`
3. ✅ Set `RESEND_API_KEY` (if sending emails)
4. ✅ Start admin app: `python3 -m admin_app.admin_app.app`
5. ✅ Open http://127.0.0.1:5055
6. ✅ Import `cases.csv` and `recipients.csv`
7. ✅ Create campaign
8. ✅ Configure campaign settings (strategy, K value)
9. ✅ Generate variants
10. ✅ Preview surveys and emails
11. ✅ Configure email template
12. ✅ Send invitation emails
13. ✅ Monitor results on Results page
14. ✅ Export data when complete

---

## Getting Help

- **Technical documentation**: See `README.md` for developer details
- **Database schema**: See `docs/database/README.md`
- **Cloudflare setup**: See `cloudflare/pages/PROVISIONING.md`

---

**License:** MIT  
**Author:** Isaac Kohane
