#!/usr/bin/env python3
"""
Quick test to verify online_assign functionality works.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "qgen"))

from admin_app.admin_app.db import Db, upsert_campaign, upsert_cases, create_invitations_for_campaign, upsert_question_items_from_cases
from qgen.contracts import CaseRow, RecipientRow

def test_online_assign():
    print("Testing online_assign functionality...")
    
    # Create temp DB
    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as f:
        db_path = f.name
    
    print(f"✓ Using temp database: {db_path}")
    
    db = Db(db_path)
    db.init()
    
    with db.connect() as conn:
        # Create test cases
        cases = [
            CaseRow(
                case_id="case_001",
                vignette="Patient presents with chest pain",
                prompt="What is your diagnosis?",
                choices=[{"id": "A", "label": "MI"}, {"id": "B", "label": "Angina"}],
                tags=["cardio"]
            ),
            CaseRow(
                case_id="case_002", 
                vignette="Patient has severe headache",
                prompt="Recommended scan?",
                choices=[{"id": "A", "label": "MRI"}, {"id": "B", "label": "CT"}],
                tags=["neuro"]
            ),
            CaseRow(
                case_id="case_003",
                vignette="Patient with abdominal pain",
                prompt="First step?",
                choices=[{"id": "A", "label": "Ultrasound"}, {"id": "B", "label": "X-ray"}],
                tags=["gastro"]
            ),
        ]
        
        upsert_cases(conn, cases)
        print(f"✓ Imported {len(cases)} cases")
        
        # Create campaign with online_assign
        campaign_id = upsert_campaign(
            conn,
            campaign_key="test_online",
            title="Test Online Assign",
            seed=42,
            questionnaire_version=1
        )
        
        # Set picker strategy to online_assign with k=2
        conn.execute(
            "UPDATE campaigns SET picker_strategy = ?, k = ? WHERE id = ?",
            ("online_assign", 2, campaign_id)
        )
        conn.commit()
        print(f"✓ Created online_assign campaign (id={campaign_id}, k=2)")
        
        # Create question bank
        n_items = upsert_question_items_from_cases(conn, campaign_id=campaign_id, cases=cases)
        print(f"✓ Created question bank with {n_items} items")
        
        # Create invitations
        recipients = [
            RecipientRow(email="alice@test.com", strata={"firstname": "Alice", "lastname": "Test"}),
            RecipientRow(email="bob@test.com", strata={"firstname": "Bob", "lastname": "Test"}),
        ]
        
        n_inv = create_invitations_for_campaign(conn, campaign_id=campaign_id, recipients=recipients)
        print(f"✓ Created {n_inv} invitations")
        
        # Get a token
        inv = conn.execute(
            "SELECT token, questionnaire_json FROM invitations WHERE campaign_id = ? LIMIT 1",
            (campaign_id,)
        ).fetchone()
        token = inv["token"]
        print(f"✓ Token: {token}")
        print(f"  Initial questionnaire_json: {inv['questionnaire_json']}")
        
        # Simulate opening the link (this should trigger assignment)
        from admin_app.admin_app.logic import assign_on_open
        
        campaign_row = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
        invitation_row = conn.execute("SELECT * FROM invitations WHERE token = ?", (token,)).fetchone()
        
        print("\n✓ Calling assign_on_open...")
        qjson = assign_on_open(conn=conn, campaign_row=campaign_row, invitation_row=invitation_row)
        conn.commit()
        
        print(f"✓ Questionnaire assigned!")
        print(f"  Title: {qjson.get('title')}")
        print(f"  Version: {qjson.get('questionnaireVersion')}")
        print(f"  Blocks: {len(qjson.get('blocks', []))}")
        
        blocks = qjson.get('blocks', [])
        for i, block in enumerate(blocks):
            print(f"    Block {i+1}: {block.get('type')} (id={block.get('id')})")
        
        # Verify snapshot was saved
        inv_after = conn.execute(
            "SELECT questionnaire_json, questionnaire_hash FROM invitations WHERE token = ?",
            (token,)
        ).fetchone()
        
        print(f"\n✓ Snapshot saved:")
        print(f"  questionnaire_hash: {inv_after['questionnaire_hash']}")
        print(f"  questionnaire_json length: {len(inv_after['questionnaire_json']) if inv_after['questionnaire_json'] else 0}")
        
        # Check assignments table
        assignments = conn.execute(
            "SELECT item_id, position FROM respondent_assignments WHERE token = ? ORDER BY position",
            (token,)
        ).fetchall()
        print(f"\n✓ Assignments created: {len(assignments)}")
        for a in assignments:
            print(f"    Position {a['position']}: {a['item_id']}")
        
        # Check question stats were updated
        stats = conn.execute(
            "SELECT item_id, assigned_count FROM question_stats WHERE campaign_id = ? ORDER BY item_id",
            (campaign_id,)
        ).fetchall()
        print(f"\n✓ Question stats:")
        for s in stats:
            print(f"    {s['item_id']}: assigned_count={s['assigned_count']}")
        
        # Test idempotency - call again
        print("\n✓ Testing idempotency (calling assign_on_open again)...")
        invitation_row2 = conn.execute("SELECT * FROM invitations WHERE token = ?", (token,)).fetchone()
        qjson2 = assign_on_open(conn=conn, campaign_row=campaign_row, invitation_row=invitation_row2)
        
        if qjson == qjson2:
            print("✓ Idempotent: Same questionnaire returned")
        else:
            print("✗ ERROR: Different questionnaire returned!")
            return False
        
        # Check stats didn't change
        stats2 = conn.execute(
            "SELECT item_id, assigned_count FROM question_stats WHERE campaign_id = ? ORDER BY item_id",
            (campaign_id,)
        ).fetchall()
        
        if list(stats) == list(stats2):
            print("✓ Stats unchanged (correct)")
        else:
            print("✗ ERROR: Stats changed on second call!")
            return False
        
    print("\n✅ online_assign works correctly!")
    return True

if __name__ == "__main__":
    try:
        success = test_online_assign()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
