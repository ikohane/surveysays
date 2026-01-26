# SurveySays

A local-first survey authoring and delivery system for creating personalized questionnaires, sending invitation emails, and collecting responses.

**Author:** Isaac Kohane  
**License:** MIT

---

## 📚 Documentation

Choose the guide that matches your role:

### 👥 [**User Guide**](README.USER.md) - For Survey Administrators
**Start here if you want to create and send surveys**

Learn how to:
- Create surveys from question banks
- Send personalized invitation emails
- Manage recipients and campaigns
- Track and export results
- Deploy to production (cloud hosting)

Perfect for researchers, study coordinators, and survey administrators.

### 🔧 [**Technical Guide**](README.TECHNICAL.md) - For Developers
**Start here if you want to understand the system architecture**

Learn about:
- System architecture and components
- Database schema and design
- API endpoints and contracts
- CSV file formats and validation
- Deployment and configuration

Perfect for developers, DevOps engineers, and technical collaborators.

---

## ⚡ Quick Start

**Prerequisites:**
- Python 3.11 or newer
- 10 minutes for setup

**Installation:**
```bash
# Navigate to the project folder
cd /path/to/SurveySays

# Install dependencies
pip install -e ./admin_app -e ./qgen

# Set up email sending (optional but recommended)
export RESEND_API_KEY='your_api_key'  # Get free key at resend.com

# Start the admin application
export PYTHONPATH="$(pwd)/qgen"
python3 -m admin_app.admin_app.app
```

**Open in browser:** http://127.0.0.1:5055

Then follow the [User Guide](README.USER.md) to create your first survey!

---

## 🎯 What Can This System Do?

✅ **Create personalized surveys** - Each recipient gets a customized question set  
✅ **Send invitation emails** - Automated email delivery with tracking  
✅ **Collect responses** - Secure, one-time submission per recipient  
✅ **Track participation** - Real-time response monitoring  
✅ **Manage recipients** - Exclude/restore individuals, add new participants  
✅ **Export results** - Download data for analysis  
✅ **Scale to production** - Optional cloud hosting via Cloudflare  

---

## 🌟 Key Features

### Local-First Design
Work offline. Your computer hosts the admin interface and manages all data locally in SQLite. No external services required for basic functionality.

### Two Delivery Modes

**Local Mode** (Testing & Small Studies)
- Surveys hosted on your machine
- Good for testing and trusted recipients
- Zero external dependencies

**Cloud Mode** (Production & Large Studies)
- Surveys hosted on Cloudflare (free tier available)
- Professional hosting with your custom domain
- Automatic result synchronization to local database

### Intelligent Question Assignment

**Pick K Cases** - Randomly select K questions per recipient  
**Online Assign** - Assign questions when recipient opens link (balances distribution)  
**Template Expand** - Generate questions from templates with variables (advanced)

### Email Integration
Professional email sending via [Resend.com](https://resend.com):
- HTML + plain text multipart emails
- Personalized variables (name, custom link, etc.)
- Preview before sending
- Rate limiting and retry logic
- Free tier: 3,000 emails/month

---

## 📊 Example Use Cases

### Clinical Case Studies
Import 50 clinical vignettes. Each physician receives 5 random cases. Track diagnostic decisions and compare across specialties.

### Survey Research
Create 100 survey questions. Each participant receives 20 questions. Balance question distribution across the participant pool.

### A/B Testing
Generate question variants from templates. Randomly assign variants to recipients. Compare response patterns.

---

## 🚀 System Components

- **`qgen/`** - Question generator (Python library)
- **`admin_app/`** - Admin web interface (Flask + SQLite)
- **`cloudflare/pages/`** - Cloud deployment (Cloudflare Pages + D1)
- **`sample_data/`** - Example CSV files and configuration

---

## 📖 Getting Started

1. **[User Guide](README.USER.md)** - Complete setup and usage instructions
2. **[Technical Guide](README.TECHNICAL.md)** - Architecture and development docs
3. **[Cloudflare Deployment](cloudflare/pages/PROVISIONING.md)** - Production hosting setup

---

## 🆘 Need Help?

**Common Questions:**
- How do I format my question file? → [User Guide: CSV Files](README.USER.md#step-1-prepare-your-data-files)
- How do I send emails? → [User Guide: Email Setup](README.USER.md#step-2-configure-email-sending-optional-but-recommended)
- How do I deploy to production? → [User Guide: Cloud Mode Setup](README.USER.md#cloud-mode-setup-optional---for-production)
- What's the database schema? → [Technical Guide: Database](README.TECHNICAL.md) or [docs/database/README.md](docs/database/README.md)

**Troubleshooting:**
See the [User Guide Troubleshooting Section](README.USER.md#troubleshooting) for common issues and solutions.

---

## 📄 License

MIT License - see [LICENSE](LICENSE) file for details.

---

**Ready to create your first survey?** → Start with the [**User Guide**](README.USER.md) 🎯
