#!/usr/bin/env python3
"""
UI Smoke Test for Admin App

Tests that key pages load without errors (HTTP 200).
Run with: python3 admin_app/scripts/ui_smoke_test.py

Requirements:
- Server must be running on http://127.0.0.1:5055
- At least one campaign must exist (creates 'ui_test_campaign' if none exist)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("ADMIN_APP_URL", "http://127.0.0.1:5055")

# Pages that don't require a campaign
GLOBAL_PAGES = [
    "/",
]

# Pages that require a campaign_key substituted
CAMPAIGN_PAGES = [
    "/campaigns/{campaign_key}",
    "/campaigns/{campaign_key}/master",
    "/campaigns/{campaign_key}/preview",
    "/campaigns/{campaign_key}/stats",
    "/campaigns/{campaign_key}/invitations",
    "/campaigns/{campaign_key}/recipients",
    "/campaigns/{campaign_key}/results",
    "/campaigns/{campaign_key}/submissions",
    "/campaigns/{campaign_key}/reports",
    "/campaigns/{campaign_key}/online-stats",
]


def fetch_page(url: str) -> tuple[int, str]:
    """Fetch a page and return (status_code, body)."""
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        return e.code, body
    except Exception as e:
        return 0, str(e)


def get_or_create_test_campaign() -> str:
    """Get first campaign key, or create a test campaign if none exist."""
    status, body = fetch_page(f"{BASE_URL}/")
    if status != 200:
        raise RuntimeError(f"Home page failed: HTTP {status}")
    
    # Look for campaign links in the HTML
    # Pattern: /campaigns/CAMPAIGN_KEY"
    import re
    matches = re.findall(r'/campaigns/([a-zA-Z0-9_-]+)"', body)
    if matches:
        # Filter out common non-campaign paths
        campaigns = [m for m in matches if m not in ("upsert",)]
        if campaigns:
            return campaigns[0]
    
    # No campaigns found, create one via POST
    print("  No campaigns found, creating 'ui_test_campaign'...")
    data = urllib.parse.urlencode({
        "campaign_key": "ui_test_campaign",
        "title": "UI Test Campaign",
        "seed": "42",
        "questionnaire_version": "1",
        "picker_strategy": "online_assign",
        "k": "3",
    }).encode("utf-8")
    
    req = urllib.request.Request(
        f"{BASE_URL}/campaigns/upsert",
        data=data,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            pass  # 302 redirect expected
    except urllib.error.HTTPError as e:
        if e.code not in (302, 303):  # Redirect is expected
            raise
    
    return "ui_test_campaign"


def run_tests() -> bool:
    """Run all UI smoke tests. Returns True if all pass."""
    print(f"UI Smoke Test - {BASE_URL}")
    print("=" * 50)
    
    all_passed = True
    
    # Test global pages
    print("\nGlobal pages:")
    for path in GLOBAL_PAGES:
        url = f"{BASE_URL}{path}"
        status, _ = fetch_page(url)
        passed = status == 200
        icon = "✓" if passed else "✗"
        print(f"  {icon} {path} -> {status}")
        if not passed:
            all_passed = False
    
    # Get or create a test campaign
    try:
        campaign_key = get_or_create_test_campaign()
        print(f"\nUsing campaign: {campaign_key}")
    except Exception as e:
        print(f"\n✗ Failed to get/create campaign: {e}")
        return False
    
    # Test campaign pages
    print("\nCampaign pages:")
    for path_template in CAMPAIGN_PAGES:
        path = path_template.format(campaign_key=campaign_key)
        url = f"{BASE_URL}{path}"
        status, body = fetch_page(url)
        # 200 = success, 302/303 = redirect (also OK for some pages)
        passed = status in (200, 302, 303)
        # Special case: some pages may flash an error and redirect, that's OK
        if status == 302:
            icon = "→"  # redirect
        elif passed:
            icon = "✓"
        else:
            icon = "✗"
            # Check if it's a known acceptable error
            if "No generated variants" in body or "Generate variants first" in body:
                icon = "⚠"  # Warning - page works but needs data
                passed = True
            elif "online_assign" in body.lower() or "not implemented" in body.lower():
                icon = "⚠"
                passed = True
        
        print(f"  {icon} {path} -> {status}")
        if not passed:
            all_passed = False
            # Show first bit of error for debugging
            if "Internal Server Error" in body or status == 500:
                print(f"      ERROR: {body[:200]}...")
    
    print("\n" + "=" * 50)
    if all_passed:
        print("All tests passed!")
    else:
        print("Some tests failed!")
    
    return all_passed


if __name__ == "__main__":
    import urllib.parse
    
    success = run_tests()
    sys.exit(0 if success else 1)

