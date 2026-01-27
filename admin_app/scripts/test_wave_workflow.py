#!/usr/bin/env python3
"""
Test script for wave-based incremental generation workflow.

Tests:
1. Create a campaign
2. Import initial recipients
3. Generate wave 1
4. Import additional recipients
5. Generate wave 2
6. Verify both waves are tracked correctly
"""
import os
import sys
import tempfile
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from admin_app.db import Db
from qgen.io_csv import parse_cases_csv, parse_recipients_csv


def test_wave_workflow():
    """Test the incremental wave generation workflow."""
    # Create a temporary database
    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as f:
        db_path = f.name
    
    print(f"Using test database: {db_path}")
    
    try:
        db = Db(db_path)
        db.init()
        
        with db.connect() as conn:
            # Import some cases
            sample_cases_path = Path(__file__).parent.parent.parent / "sample_data" / "cases.csv"
            with open(sample_cases_path) as f:
                cases_text = f.read()
            
            from qgen.io_csv import parse_cases_csv
            from admin_app.db import upsert_cases
            cases = parse_cases_csv(cases_text)
            n_cases = upsert_cases(conn, cases)
            print(f"✓ Imported {n_cases} cases")
            
            # Create a campaign
            from admin_app.db import upsert_campaign
            upsert_campaign(
                conn,
                campaign_key="test_wave_campaign",
                title="Test Wave Campaign",
                seed=42,
                questionnaire_version=1,
            )
            # Set picker strategy
            conn.execute(
                "UPDATE campaigns SET picker_strategy = ?, k = ? WHERE campaign_key = ?",
                ("pick_k_cases", 2, "test_wave_campaign"),
            )
            campaign = conn.execute(
                "SELECT * FROM campaigns WHERE campaign_key = ?",
                ("test_wave_campaign",),
            ).fetchone()
            campaign_id = int(campaign["id"])
            print(f"✓ Created campaign: {campaign['campaign_key']} (id={campaign_id})")
            
            # Import initial recipients (first 3)
            from admin_app.db import upsert_recipients
            from qgen.contracts import RecipientRow
            initial_recipients = [
                RecipientRow(
                    email=f"user{i}@example.com",
                    strata={"firstname": f"User{i}", "lastname": "Test"},
                )
                for i in range(1, 4)
            ]
            n_recip = upsert_recipients(conn, initial_recipients)
            print(f"✓ Imported {n_recip} initial recipients")
            
            # Generate Wave 1
            from admin_app.db import (
                load_cases,
                load_recipients,
                get_next_wave_number,
                get_recipients_with_variants,
                insert_generation_wave,
                insert_variants,
            )
            from qgen.generator import generate_bulk_payload
            
            all_cases = load_cases(conn)
            all_recipients = load_recipients(conn)
            
            wave_number = get_next_wave_number(conn, campaign_id=campaign_id)
            existing_emails = get_recipients_with_variants(conn, campaign_id=campaign_id)
            new_recipients = [r for r in all_recipients if r.email.lower() not in existing_emails]
            
            print(f"  Wave {wave_number}: {len(new_recipients)} new recipients to process")
            
            payload = generate_bulk_payload(
                campaign_key="test_wave_campaign",
                title="Test Wave Campaign",
                questionnaire_version=1,
                cases=all_cases,
                recipients=new_recipients,
                seed=42,
                picker_strategy="pick_k_cases",
                k=2,
            )
            
            wave_id = insert_generation_wave(
                conn,
                campaign_id=campaign_id,
                wave_number=wave_number,
                picker_strategy="pick_k_cases",
                k=2,
                seed=42,
                recipients_processed=len(new_recipients),
                variants_created=len(payload["invitations"]),
            )
            
            insert_variants(
                conn,
                campaign_id=campaign_id,
                variants=payload["invitations"],
                wave_id=wave_id,
            )
            
            print(f"✓ Generated Wave 1: {len(payload['invitations'])} variants for {len(new_recipients)} recipients")
            
            # Import additional recipients (2 more)
            additional_recipients = [
                RecipientRow(
                    email=f"user{i}@example.com",
                    strata={"firstname": f"User{i}", "lastname": "Test"},
                )
                for i in range(4, 6)
            ]
            n_recip = upsert_recipients(conn, additional_recipients)
            print(f"✓ Imported {n_recip} additional recipients")
            
            # Generate Wave 2
            all_recipients = load_recipients(conn)
            wave_number = get_next_wave_number(conn, campaign_id=campaign_id)
            existing_emails = get_recipients_with_variants(conn, campaign_id=campaign_id)
            new_recipients = [r for r in all_recipients if r.email.lower() not in existing_emails]
            
            print(f"  Wave {wave_number}: {len(new_recipients)} new recipients to process")
            
            payload = generate_bulk_payload(
                campaign_key="test_wave_campaign",
                title="Test Wave Campaign",
                questionnaire_version=1,
                cases=all_cases,
                recipients=new_recipients,
                seed=42,
                picker_strategy="pick_k_cases",
                k=2,
            )
            
            wave_id = insert_generation_wave(
                conn,
                campaign_id=campaign_id,
                wave_number=wave_number,
                picker_strategy="pick_k_cases",
                k=2,
                seed=42,
                recipients_processed=len(new_recipients),
                variants_created=len(payload["invitations"]),
            )
            
            insert_variants(
                conn,
                campaign_id=campaign_id,
                variants=payload["invitations"],
                wave_id=wave_id,
            )
            
            print(f"✓ Generated Wave 2: {len(payload['invitations'])} variants for {len(new_recipients)} recipients")
            
            # Verify wave tracking
            from admin_app.db import list_generation_waves, variant_counts
            
            waves = list_generation_waves(conn, campaign_id=campaign_id)
            counts = variant_counts(conn, campaign_id=campaign_id)
            
            print(f"\n=== Wave Summary ===")
            print(f"Total waves: {len(waves)}")
            for wave in waves:
                print(f"  Wave {wave['wave_number']}: {wave['recipients_processed']} recipients, {wave['variants_created']} variants")
            
            print(f"\nTotal variants: {counts['total']}")
            print(f"Distinct hashes: {counts['distinct_hashes']}")
            
            # Verify all recipients have variants
            existing_emails = get_recipients_with_variants(conn, campaign_id=campaign_id)
            all_recipients = load_recipients(conn)
            print(f"\nRecipients with variants: {len(existing_emails)} of {len(all_recipients)}")
            
            if len(existing_emails) == len(all_recipients):
                print("✓ All recipients have variants!")
            else:
                print("✗ Some recipients missing variants")
                return False
            
            # Verify wave_id is set on variants
            variants_with_wave = conn.execute(
                "SELECT COUNT(*) as n FROM invitation_variants WHERE campaign_id = ? AND wave_id IS NOT NULL",
                (campaign_id,),
            ).fetchone()["n"]
            
            print(f"Variants with wave_id: {variants_with_wave} of {counts['total']}")
            
            if variants_with_wave == counts['total']:
                print("✓ All variants have wave_id!")
            else:
                print("✗ Some variants missing wave_id")
                return False
            
            conn.commit()
        
        print("\n✅ All tests passed!")
        return True
        
    finally:
        # Clean up
        if os.path.exists(db_path):
            os.unlink(db_path)
            print(f"\nCleaned up test database: {db_path}")


if __name__ == "__main__":
    success = test_wave_workflow()
    sys.exit(0 if success else 1)

