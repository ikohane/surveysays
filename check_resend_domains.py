#!/usr/bin/env python3
import os
import json
import urllib.request
import urllib.error

# Get API key from environment
api_key = os.environ.get("RESEND_API_KEY", "").strip()
if not api_key:
    print("❌ RESEND_API_KEY not set in environment")
    exit(1)

# Check domains
try:
    req = urllib.request.Request(
        "https://api.resend.com/domains",
        headers={"Authorization": f"Bearer {api_key}"}
    )
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
        
    print("📧 Resend Domains:\n")
    if "data" in data:
        for domain in data["data"]:
            status = "✅" if domain.get("status") == "verified" else "⚠️"
            print(f"{status} {domain.get('name')} - {domain.get('status')}")
            if domain.get("status") != "verified":
                print(f"   Region: {domain.get('region')}")
                print(f"   Records needed:")
                for record in domain.get("records", []):
                    print(f"     - {record.get('record_type')}: {record.get('name')} → {record.get('value')}")
        print()
    else:
        print(data)
        
except urllib.error.HTTPError as e:
    print(f"❌ API Error: {e.code} {e.reason}")
    print(e.read().decode())
except Exception as e:
    print(f"❌ Error: {e}")
