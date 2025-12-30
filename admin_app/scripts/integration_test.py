from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main() -> None:
    repo = _repo_root()

    # Make repo packages importable (admin_app and qgen live here)
    sys.path.insert(0, str(repo))

    # Make qgen importable (admin_app depends on it)
    sys.path.insert(0, str(repo / "qgen"))

    # Use an isolated DB for the test run
    db_path = repo / "out" / "integration_test.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    os.environ["ADMIN_APP_DB"] = str(db_path)
    os.environ["ADMIN_APP_SECRET"] = "integration-test-secret"

    from admin_app.admin_app.app import create_app  # noqa: E402
    from qgen.validation import validate_questionnaire_json  # noqa: E402

    app = create_app()
    client = app.test_client()

    # 1) Home should load
    r = client.get("/")
    assert r.status_code == 200, f"GET / expected 200, got {r.status_code}"

    # 2) Import cases.csv + recipients.csv
    cases_csv = (repo / "sample_data" / "cases.csv").read_bytes()
    recs_csv = (repo / "sample_data" / "recipients.csv").read_bytes()

    r = client.post(
        "/imports/cases",
        data={"file": (io.BytesIO(cases_csv), "cases.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert b"Imported" in r.data, "Expected success flash after importing cases"

    r = client.post(
        "/imports/recipients",
        data={"file": (io.BytesIO(recs_csv), "recipients.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert b"Imported" in r.data, "Expected success flash after importing recipients"

    def create_campaign(*, campaign_key: str, picker_strategy: str, k: int) -> None:
        r = client.post(
            "/campaigns/upsert",
            data={
                "campaign_key": campaign_key,
                "title": "Integration Test Survey",
                "seed": "12345",
                "questionnaire_version": "1",
                "picker_strategy": picker_strategy,
                "k": str(k),
            },
            follow_redirects=True,
        )
        assert r.status_code == 200
        assert campaign_key.encode("utf-8") in r.data

    def generate(campaign_key: str) -> None:
        r = client.post(f"/campaigns/{campaign_key}/generate", follow_redirects=True)
        assert r.status_code == 200
        assert (b"Generated" in r.data) or (b"Online mode ready" in r.data), "Expected success flash after action"

    def export_payload(campaign_key: str) -> dict:
        resp = client.get(f"/campaigns/{campaign_key}/export.json")
        assert resp.status_code == 200, f"export status {resp.status_code}"
        payload = json.loads(resp.data.decode("utf-8"))
        assert payload["campaignKey"] == campaign_key
        assert isinstance(payload["invitations"], list)
        return payload

    # 3) Pick-K strategy with K=2
    campaign_pickk = "it_pickk"
    create_campaign(campaign_key=campaign_pickk, picker_strategy="pick_k_cases", k=2)
    generate(campaign_pickk)

    # Master view should render for offline campaigns
    r = client.get(f"/campaigns/{campaign_pickk}/master")
    assert r.status_code == 200
    assert b"Master view" in r.data
    assert b"Generate variants" in r.data or b"Generate" in r.data

    # Offline campaigns should also have tokenized invitations with snapshots after Generate
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    campaign_id_pickk = int(conn.execute("SELECT id FROM campaigns WHERE campaign_key = ?", (campaign_pickk,)).fetchone()["id"])
    inv = conn.execute(
        "SELECT token, questionnaire_json FROM invitations WHERE campaign_id = ? ORDER BY email LIMIT 1",
        (campaign_id_pickk,),
    ).fetchone()
    assert inv is not None
    assert inv["token"]
    assert inv["questionnaire_json"], "offline invitation should have snapshotted questionnaire_json"
    conn.close()

    r = client.get(f"/s/{inv['token']}")
    assert r.status_code == 200
    assert b"Respondent view" in r.data

    # Offline submit should work too (one-and-done)
    qj_off = json.loads(inv["questionnaire_json"])
    answers_form_off = {}
    for b in qj_off["blocks"]:
        if b["type"] == "singleSelect":
            answers_form_off[f"ans__{b['id']}"] = b["choices"][0]["id"]
    r = client.post(f"/s/{inv['token']}/submit", data=answers_form_off, follow_redirects=False)
    assert r.status_code in (302, 303)
    r = client.post(f"/s/{inv['token']}/submit", data=answers_form_off)
    assert r.status_code == 409

    payload1 = export_payload(campaign_pickk)
    assert len(payload1["invitations"]) == 5
    inv0 = payload1["invitations"][0]
    qj = inv0["questionnaireJson"]
    assert len(qj["blocks"]) == 4, "K=2 should yield 2*(vignette+question)=4 blocks"
    assert qj["blocks"][0]["id"] == "vignette_1"
    assert qj["blocks"][1]["id"] == "decision_1"
    assert qj["blocks"][2]["id"] == "vignette_2"
    assert qj["blocks"][3]["id"] == "decision_2"

    # determinism
    generate(campaign_pickk)
    payload2 = export_payload(campaign_pickk)
    assert payload1 == payload2, "Pick-K should be deterministic"

    # 4) Template expansion strategy with K=2 (requires templates + param_vector upload)
    templates_csv = (repo / "sample_data" / "templates.csv").read_bytes()
    r = client.post(
        "/imports/templates",
        data={"file": (io.BytesIO(templates_csv), "templates.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert b"Imported" in r.data, "Expected success flash after importing templates"

    campaign_tmpl = "it_template"
    create_campaign(campaign_key=campaign_tmpl, picker_strategy="template_expand", k=2)

    pv = (repo / "sample_data" / "param_vector.json").read_bytes()
    r = client.post(
        f"/campaigns/{campaign_tmpl}/param-vector",
        data={"file": (io.BytesIO(pv), "param_vector.json")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert b"Saved param_vector" in r.data

    generate(campaign_tmpl)
    payload_t1 = export_payload(campaign_tmpl)
    assert len(payload_t1["invitations"]) == 5
    qj = payload_t1["invitations"][0]["questionnaireJson"]
    assert len(qj["blocks"]) == 4
    # spot-check rendered text contains no braces (placeholders should be filled)
    assert "{" not in qj["blocks"][0]["text"]

    # determinism
    generate(campaign_tmpl)
    payload_t2 = export_payload(campaign_tmpl)
    assert payload_t1 == payload_t2, "Template expansion should be deterministic"

    # 5) Online assignment strategy (assignment happens on link-open)
    campaign_online = "it_online"
    create_campaign(campaign_key=campaign_online, picker_strategy="online_assign", k=2)
    generate(campaign_online)

    # Master view should render for online_assign campaigns and include email controls
    r = client.get(f"/campaigns/{campaign_online}/master")
    assert r.status_code == 200
    assert b"Master view" in r.data
    assert b"Prepare online_assign" in r.data
    assert b"Email (Resend) settings" in r.data
    assert b"Send invitation emails" in r.data

    resp = client.get(f"/campaigns/{campaign_online}/export_invitations.json")
    assert resp.status_code == 200
    inv_payload = json.loads(resp.data.decode("utf-8"))
    assert inv_payload["campaignKey"] == campaign_online
    assert len(inv_payload["invitations"]) == 5
    token0 = inv_payload["invitations"][0]["token"]
    assert token0

    # First open assigns and snapshots
    r1 = client.get(f"/s/{token0}")
    assert r1.status_code == 200

    # Second open is idempotent
    r2 = client.get(f"/s/{token0}")
    assert r2.status_code == 200

    # Verify DB state changed exactly once
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    campaign_id = int(conn.execute("SELECT id FROM campaigns WHERE campaign_key = ?", (campaign_online,)).fetchone()["id"])

    inv_row = conn.execute(
        "SELECT opened_at, questionnaire_json, questionnaire_hash FROM invitations WHERE token = ?",
        (token0,),
    ).fetchone()
    assert inv_row is not None
    assert inv_row["opened_at"] is not None
    assert inv_row["questionnaire_hash"]
    qj = json.loads(inv_row["questionnaire_json"])
    assert len(qj["blocks"]) == 4

    n_assign = int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM respondent_assignments WHERE campaign_id = ? AND token = ?",
            (campaign_id, token0),
        ).fetchone()["n"]
    )
    assert n_assign == 2

    sum_assigned_1 = conn.execute(
        "SELECT COALESCE(SUM(assigned_count),0) AS s FROM question_stats WHERE campaign_id = ?",
        (campaign_id,),
    ).fetchone()["s"]
    assert int(sum_assigned_1) == 2

    # Another open should not increment assigned_count
    _ = client.get(f"/s/{token0}")
    sum_assigned_2 = conn.execute(
        "SELECT COALESCE(SUM(assigned_count),0) AS s FROM question_stats WHERE campaign_id = ?",
        (campaign_id,),
    ).fetchone()["s"]
    assert int(sum_assigned_2) == 2
    conn.close()

    # 5b) Submit responses (one-and-done) + reports cohorts
    # Build minimal answers map from snapshot blocks (only answerable required blocks).
    inv_row = sqlite3.connect(str(db_path))
    inv_row.row_factory = sqlite3.Row
    row = inv_row.execute("SELECT questionnaire_json FROM invitations WHERE token = ?", (token0,)).fetchone()
    assert row is not None
    qj = json.loads(row["questionnaire_json"])
    answers_form = {}
    for b in qj["blocks"]:
        if b["type"] == "singleSelect":
            answers_form[f"ans__{b['id']}"] = b["choices"][0]["id"]
        if b["type"] == "freeText":
            answers_form[f"ans__{b['id']}"] = "test"
    inv_row.close()

    r = client.post(f"/s/{token0}/submit", data=answers_form, follow_redirects=False)
    assert r.status_code in (302, 303), f"expected redirect after submit, got {r.status_code}"

    # Repeat submit returns 409
    r = client.post(f"/s/{token0}/submit", data=answers_form)
    assert r.status_code == 409

    # Reports page renders and cohort counts show submitted==1
    r = client.get(f"/campaigns/{campaign_online}/reports")
    assert r.status_code == 200
    assert b"Submitted" in r.data

    # 6) freeText block (contract + rendering)
    q_free = {
        "title": "FreeText Test",
        "questionnaireVersion": 1,
        "blocks": [
            {"type": "vignette", "id": "v1", "text": "Case vignette"},
            {"type": "freeText", "id": "ft1", "prompt": "In one sentence, what would you do?", "required": True},
            {
                "type": "singleSelect",
                "id": "ss1",
                "prompt": "Choose one",
                "required": True,
                "choices": [{"id": "A", "label": "A"}, {"id": "B", "label": "B"}],
            },
        ],
    }
    validate_questionnaire_json(q_free)
    with app.test_request_context("/"):
        html = app.jinja_env.get_template("preview.html").render(
            campaign={"campaign_key": "it_freeText"},
            email="freeText@example.com",
            qjson=q_free,
        )
        assert "Free Text" in html

    print("Integration test passed.")


if __name__ == "__main__":
    main()


