# SurveySays

Create and send personalized surveys with ease. Each recipient gets a custom set of questions delivered via email invitation. Track responses in real-time and export results for analysis.

**Author:** Isaac Kohane  
**License:** MIT

---

## Quick Start (5 Minutes)

**Prerequisites:** Python 3.11 or newer

```bash
# 1. Navigate to the project folder
cd /path/to/SurveySays

# 2. Install dependencies
pip install -e ./admin_app -e ./qgen

# 3. Start the application
export PYTHONPATH="$(pwd)/qgen"
python3 -m admin_app.admin_app.app
```

**Open in browser:** http://127.0.0.1:5055

The app starts in **Basic mode** by default - a simplified interface perfect for most users.

---

## What You Can Do

Create personalized surveys where each recipient gets different questions:

- **Upload question banks** - CSV files with your questions
- **Manage recipients** - Email lists and participant tracking
- **Two survey types** - Pre-assigned or Dynamic Assignment
- **Styled email invitations** - Professional templates with your branding
- **Real-time tracking** - See who opened and submitted
- **Cloud deployment** - Host surveys on professional platforms
- **Export results** - Download data for analysis

---

## Create Your First Survey

### Step 1: Upload Your Data

**Questions File (cases.csv):**
```csv
case_id,vignette,prompt,choice_A,choice_B,choice_C,choice_D
case_001,"65M with chest pain...","What is the best next step?","Aspirin + activate cath lab","Order outpatient stress test","Discharge with reassurance","Give ibuprofen and observe"
```

**Recipients File (recipients.csv):**
```csv
email,firstname,lastname
alice@example.com,Alice,Smith
bob@example.com,Bob,Jones
```

In the app:
1. Click **"Upload questions"** and select your `cases.csv`
2. Click **"Upload recipients"** and select your `recipients.csv`

### Step 2: Choose Your Survey Type

**Pre-assigned Questions:**
- Generate personalized question sets before sending invitations
- You can review what each person will see
- Perfect for smaller studies or when you want full control
- Choose when: You want to verify questions before sending

**Dynamic Assignment:**
- Questions assigned automatically when recipients open their survey link
- Ensures even distribution across all recipients (load-balanced)
- Great for large studies and balanced question coverage
- Choose when: You have many recipients and want optimal question distribution

Select **Questions per recipient (K)** - how many questions each person gets (e.g., 2, 5, 10).

### Step 3: Generate Questions

Click **"Generate Questions"** button.

The system creates personalized question sets for each recipient. You'll see a status message showing how many recipients have questions ready.

### Step 4: Customize Your Email

**Email Settings:**
- **From:** Choose from preset addresses (zak@study.hvp.global, payal@study.hvp.global, info@study.hvp.global)
- **Subject:** Your invitation subject line (e.g., "You're invited to participate in our study")
- **Message:** Professional HTML template is provided by default

The default template includes:
- Personalized greeting with recipient's name
- Study title and invitation
- Prominent "Begin Survey" button
- Backup link for accessibility

**Live Preview:** Edit the HTML and click "Update Preview" to see how it looks.

### Step 5: Deploy to Cloud

Click **"Push to Cloud"** to upload your survey to professional hosting.

The system automatically chooses the right platform:
- Pre-assigned Questions → Cloudflare Pages (fast, global CDN)
- Dynamic Assignment → Railway (real-time database)

You'll see a confirmation showing how many recipients are ready.

### Step 6: Send Invitations

Click **"Send Emails"** to send invitation emails to all recipients.

Each email contains:
- Personalized greeting
- Unique survey link for that recipient
- Professional formatting

**Testing Mode:** By default, all emails go to your test address (configured via `EMAIL_TESTING_OVERRIDE`). Remove this setting when ready for production.

### Step 7: Track Responses

**Recipient Status** shows:
- Email sent status
- Survey opened
- Survey submitted

**Export Results** when ready for analysis.

---

## Common Tasks

### Adding More Recipients

1. Upload a new recipients CSV with additional people
2. Click "Generate Questions" - only new recipients get questions
3. Push to cloud again
4. Send emails to the new batch

### Sending Follow-up Emails

The system tracks who hasn't responded. You can re-send invitations to specific recipients.

### Changing Email Templates

Edit the HTML in Email Settings and click "Update Preview" to see changes immediately. The system supports full HTML/CSS styling for professional-looking emails.

### Reviewing Responses

Go to the campaign page to see:
- Response counts and completion rates
- Individual responses (by question)
- Export data as CSV for analysis

---

## Need More Control? (Expert Mode)

Most users won't need Expert mode, but it's available if you want:

- Manual configuration of all settings
- Direct YAML editing for email and layout
- Advanced picker strategy options
- Wave management for question generation
- Detailed event logs and debugging

**Switch to Expert mode:** Toggle switch in the top-right corner of any page.

See [Expert Mode Features](#expert-mode-features) below for details.

---

## Expert Mode Features

For power users who need advanced control:

### Advanced Configuration
- Direct YAML editing for email templates and layouts
- Manual picker strategy selection (pick_k_cases, online_assign, template_expand)
- Custom question generation parameters
- Template-based question generation with variable substitution

### Wave Management
- Generate questions in multiple waves
- Track generation history
- Add recipients to existing campaigns

### Technical Details
- View event logs with timestamps
- See database IDs and technical fields
- Access to all configuration options
- Direct control over cloud deployment endpoints

### When to Use Expert Mode
- You're comfortable with YAML
- You need template-based generation
- You want to see technical implementation details
- You're debugging or developing features

**Detailed Guide:** See [README.USER.md](README.USER.md) for complete Expert mode documentation.

---

## Cloud Deployment Options

### Cloudflare Pages (Pre-assigned Questions)
- Free tier available
- Global CDN for fast loading
- Custom domain support
- Automatic HTTPS

Setup guide: [cloudflare/pages/PROVISIONING.md](cloudflare/pages/PROVISIONING.md)

### Railway (Dynamic Assignment)
- Free tier available
- PostgreSQL database included
- Auto-deploy from GitHub
- Real-time question assignment

Setup guide: [docs/RAILWAY_DEPLOYMENT.md](docs/RAILWAY_DEPLOYMENT.md)

### Email Sending (Resend.com)
- 3,000 emails/month free tier
- Professional deliverability
- HTML + plain text emails
- Custom domain support

Get API key: https://resend.com

**Set environment variable:**
```bash
export RESEND_API_KEY='your_api_key_here'
```

---

## Troubleshooting

### "No questions imported yet"
Upload your `cases.csv` file first (Step 1).

### "No recipients imported yet"
Upload your `recipients.csv` file first (Step 1).

### "Email sending failed"
- Check that `RESEND_API_KEY` is set
- Verify your domain in Resend dashboard
- Ensure sender email matches verified domain

### "Cloud push failed: 403"
- Check that `CLOUDFLARE_ADMIN_TOKEN` is set correctly
- Verify your IP isn't blocked by Cloudflare WAF
- Ensure `CLOUDFLARE_STUDY_BASE_URL` is correct

### Emails going to wrong address
Set `EMAIL_TESTING_OVERRIDE` environment variable:
```bash
export EMAIL_TESTING_OVERRIDE="your-test-email@example.com"
```
Unset it for production sends.

---

## Example Use Cases

### Clinical Case Studies
Import 50 clinical vignettes. Each physician receives 5 random cases. Track diagnostic decisions and compare across specialties.

### Survey Research
Create 100 survey questions. Each participant receives 20 questions. Ensure balanced distribution across the participant pool with Dynamic Assignment.

### Educational Assessment
Upload exam questions. Each student gets a unique set. Prevent sharing while maintaining consistent difficulty.

### A/B Testing
Test different question phrasings or presentation styles with randomly assigned variants.

---

## Documentation Index

- **[User Guide](README.USER.md)** - Complete setup and usage instructions for both Basic and Expert modes
- **[Developer Guide](README.DEVELOPER.md)** - Technical architecture, database schema, and API documentation
- **[Cloudflare Deployment](cloudflare/pages/PROVISIONING.md)** - Production hosting setup for Pre-assigned Questions
- **[Railway Deployment](docs/RAILWAY_DEPLOYMENT.md)** - Production hosting setup for Dynamic Assignment
- **[Database Schema](docs/database/README.md)** - Database design and relationships

---

## For Developers

Interested in the technical architecture or contributing to the project?

**System Architecture:**
- `qgen/` - Question generator library (Python)
- `admin_app/` - Admin web interface (Flask + SQLite)
- `cloudflare/pages/` - Cloud respondent delivery (Cloudflare Pages + D1)
- Railway deployment for online assignment with PostgreSQL

**Key Technologies:**
- Python 3.11+, Flask, SQLite
- TypeScript for Cloudflare Workers
- Jinja2 templating for dual UI mode
- Resend API for email delivery

**See [README.DEVELOPER.md](README.DEVELOPER.md)** for complete technical documentation.

---

## License

MIT License - see [LICENSE](LICENSE) file for details.

---

**Ready to get started?** Follow the [Quick Start](#quick-start-5-minutes) above, or dive into the [User Guide](README.USER.md) for detailed instructions.
