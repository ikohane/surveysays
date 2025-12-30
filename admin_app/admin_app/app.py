from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from flask import Flask, Response, flash, redirect, render_template, request, url_for

from qgen.generator import generate_bulk_payload
from qgen.io_csv import parse_cases_csv, parse_recipients_csv
from qgen.io_templates_csv import parse_templates_csv
from qgen.templates_contracts import parse_param_vector_json
from qgen.qpicker.base import QuestionUnit, build_questionnaire_from_units

from .db import (
    Db,
    clear_variants_for_campaign,
    clear_question_bank,
    create_invitations_for_campaign,
    get_campaign_by_key,
    get_invitation_by_token,
    get_question_item,
    has_submission,
    increment_assigned_count,
    increment_submitted_count,
    insert_submission,
    insert_submission_answer,
    insert_variants,
    list_campaigns,
    list_assignments_for_token,
    list_invitations_for_campaign,
    list_question_items_with_stats,
    load_cases,
    load_recipients,
    load_templates,
    mark_invitation_opened,
    populate_invitations_from_variants,
    report_rows,
    save_invitation_snapshot,
    insert_assignment,
    submission_cohort_counts,
    upsert_campaign,
    upsert_cases,
    upsert_recipients,
    upsert_templates,
    upsert_question_items_from_cases,
    variant_counts,
)

from .resend_client import ResendError, create_or_update_campaign_template, send_invites_for_campaign


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("ADMIN_APP_SECRET", "dev-secret-change-me")

    repo_root = Path(__file__).resolve().parents[2]
    db_path = Path(os.environ.get("ADMIN_APP_DB", str(repo_root / "out" / "local_admin.sqlite3")))
    db = Db(db_path)
    db.init()

    @app.get("/")
    def home() -> str:
        with db.connect() as conn:
            campaigns = list_campaigns(conn)
            templates_count = conn.execute("SELECT COUNT(*) AS n FROM templates").fetchone()["n"]
        return render_template("home.html", campaigns=campaigns, db_path=str(db_path), templates_count=templates_count)

    @app.post("/campaigns/upsert")
    def campaigns_upsert() -> Response:
        campaign_key = (request.form.get("campaign_key") or "").strip()
        title = (request.form.get("title") or "").strip()
        seed_raw = (request.form.get("seed") or "").strip()
        version_raw = (request.form.get("questionnaire_version") or "").strip()
        picker_strategy = (request.form.get("picker_strategy") or "pick_k_cases").strip()
        k_raw = (request.form.get("k") or "1").strip()

        if not campaign_key or not title:
            flash("campaign_key and title are required", "error")
            return redirect(url_for("home"))

        try:
            seed = int(seed_raw)
            version = int(version_raw)
            k = int(k_raw)
        except ValueError:
            flash("seed, questionnaire_version, and k must be integers", "error")
            return redirect(url_for("home"))

        if k < 1:
            flash("k must be >= 1", "error")
            return redirect(url_for("home"))

        with db.connect() as conn:
            upsert_campaign(
                conn,
                campaign_key=campaign_key,
                title=title,
                seed=seed,
                questionnaire_version=version,
            )
            # update picker columns (may exist via migration)
            conn.execute(
                """
                UPDATE campaigns
                SET picker_strategy = ?, k = ?
                WHERE campaign_key = ?
                """,
                (picker_strategy, k, campaign_key),
            )
            conn.commit()
        flash(f"Saved campaign '{campaign_key}'", "success")
        return redirect(url_for("campaign_detail", campaign_key=campaign_key))

    @app.get("/campaigns/<campaign_key>")
    def campaign_detail(campaign_key: str) -> str:
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                flash("Campaign not found", "error")
                return redirect(url_for("home"))
            cases = load_cases(conn)
            recipients = load_recipients(conn)
            templates = load_templates(conn)
            counts = variant_counts(conn, campaign_id=int(campaign["id"]))
            inv_counts = None
            try:
                if campaign["picker_strategy"] == "online_assign":
                    inv_counts = conn.execute(
                        """
                        SELECT
                          COUNT(*) AS total,
                          SUM(CASE WHEN opened_at IS NOT NULL THEN 1 ELSE 0 END) AS opened,
                          SUM(CASE WHEN questionnaire_hash IS NOT NULL THEN 1 ELSE 0 END) AS assigned
                        FROM invitations
                        WHERE campaign_id = ?
                        """,
                        (int(campaign["id"]),),
                    ).fetchone()
            except Exception:
                inv_counts = None
        return render_template(
            "campaign.html",
            campaign=campaign,
            cases_count=len(cases),
            recipients_count=len(recipients),
            templates_count=len(templates),
            counts=counts,
            inv_counts=inv_counts,
        )

    @app.post("/campaigns/<campaign_key>/settings")
    def update_campaign_settings(campaign_key: str) -> Response:
        picker_strategy = (request.form.get("picker_strategy") or "").strip()
        k_raw = (request.form.get("k") or "").strip()
        if picker_strategy not in ("pick_k_cases", "template_expand", "online_assign"):
            flash("picker_strategy must be pick_k_cases, template_expand, or online_assign", "error")
            return redirect(url_for("campaign_detail", campaign_key=campaign_key))
        try:
            k = int(k_raw)
        except ValueError:
            flash("k must be an integer", "error")
            return redirect(url_for("campaign_detail", campaign_key=campaign_key))
        if k < 1:
            flash("k must be >= 1", "error")
            return redirect(url_for("campaign_detail", campaign_key=campaign_key))

        with db.connect() as conn:
            conn.execute(
                "UPDATE campaigns SET picker_strategy = ?, k = ? WHERE campaign_key = ?",
                (picker_strategy, k, campaign_key),
            )
            conn.commit()
        flash("Updated picker settings", "success")
        return redirect(url_for("campaign_detail", campaign_key=campaign_key))

    @app.post("/imports/cases")
    def import_cases() -> Response:
        f = request.files.get("file")
        if not f:
            flash("Please choose a cases.csv file to upload", "error")
            return redirect(request.referrer or url_for("home"))
        try:
            text = f.stream.read().decode("utf-8")
            cases = parse_cases_csv(text)
        except Exception as e:
            flash(f"Failed to parse cases.csv: {e}", "error")
            return redirect(request.referrer or url_for("home"))

        with db.connect() as conn:
            n = upsert_cases(conn, cases)
            conn.commit()
        flash(f"Imported {n} cases", "success")
        return redirect(request.referrer or url_for("home"))

    @app.post("/imports/recipients")
    def import_recipients() -> Response:
        f = request.files.get("file")
        if not f:
            flash("Please choose a recipients.csv file to upload", "error")
            return redirect(request.referrer or url_for("home"))
        try:
            text = f.stream.read().decode("utf-8")
            recs = parse_recipients_csv(text)
        except Exception as e:
            flash(f"Failed to parse recipients.csv: {e}", "error")
            return redirect(request.referrer or url_for("home"))

        with db.connect() as conn:
            n = upsert_recipients(conn, recs)
            conn.commit()
        flash(f"Imported {n} recipients", "success")
        return redirect(request.referrer or url_for("home"))

    @app.post("/imports/templates")
    def import_templates() -> Response:
        f = request.files.get("file")
        if not f:
            flash("Please choose a templates.csv file to upload", "error")
            return redirect(request.referrer or url_for("home"))
        try:
            text = f.stream.read().decode("utf-8")
            templates = parse_templates_csv(text)
        except Exception as e:
            flash(f"Failed to parse templates.csv: {e}", "error")
            return redirect(request.referrer or url_for("home"))

        with db.connect() as conn:
            n = upsert_templates(conn, templates)
            conn.commit()
        flash(f"Imported {n} templates", "success")
        return redirect(request.referrer or url_for("home"))

    @app.post("/campaigns/<campaign_key>/param-vector")
    def upload_param_vector(campaign_key: str) -> Response:
        f = request.files.get("file")
        if not f:
            flash("Please choose a param_vector.json file to upload", "error")
            return redirect(url_for("campaign_detail", campaign_key=campaign_key))
        try:
            obj = json.loads(f.stream.read().decode("utf-8"))
            parse_param_vector_json(obj)
        except Exception as e:
            flash(f"Failed to parse param_vector.json: {e}", "error")
            return redirect(url_for("campaign_detail", campaign_key=campaign_key))

        with db.connect() as conn:
            conn.execute(
                "UPDATE campaigns SET param_vector_json = ? WHERE campaign_key = ?",
                (json.dumps(obj, ensure_ascii=False), campaign_key),
            )
            conn.commit()
        flash("Saved param_vector.json for campaign", "success")
        return redirect(url_for("campaign_detail", campaign_key=campaign_key))

    @app.post("/campaigns/<campaign_key>/generate")
    def generate_variants(campaign_key: str) -> Response:
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                flash("Campaign not found", "error")
                return redirect(url_for("home"))
            cases = load_cases(conn)
            recipients = load_recipients(conn)
            templates = load_templates(conn)
            if not recipients:
                flash("No recipients imported yet. Import recipients.csv first.", "error")
                return redirect(url_for("campaign_detail", campaign_key=campaign_key))

            picker_strategy = (campaign["picker_strategy"] if "picker_strategy" in campaign.keys() else "pick_k_cases")  # type: ignore[attr-defined]
            k = int(campaign["k"]) if "k" in campaign.keys() else 1  # type: ignore[attr-defined]

            if picker_strategy == "online_assign":
                if not cases:
                    flash("No cases imported yet. Import cases.csv first.", "error")
                    return redirect(url_for("campaign_detail", campaign_key=campaign_key))
                # Build question bank + invitations; assignment happens on /s/<token>.
                clear_variants_for_campaign(conn, campaign_id=int(campaign["id"]))
                clear_question_bank(conn, campaign_id=int(campaign["id"]))
                n_items = upsert_question_items_from_cases(conn, campaign_id=int(campaign["id"]), cases=cases)
                n_inv = create_invitations_for_campaign(conn, campaign_id=int(campaign["id"]), recipients=recipients)
                conn.commit()
                flash(f"Online mode ready: {n_inv} new invitations, {n_items} question items", "success")
                return redirect(url_for("campaign_invitations", campaign_key=campaign_key))

            templates_csv_text: str | None = None
            param_vector_obj: dict[str, Any] | None = None

            if picker_strategy == "pick_k_cases":
                if not cases:
                    flash("No cases imported yet. Import cases.csv first.", "error")
                    return redirect(url_for("campaign_detail", campaign_key=campaign_key))
            elif picker_strategy == "template_expand":
                if not templates:
                    flash("No templates imported yet. Import templates.csv first.", "error")
                    return redirect(url_for("campaign_detail", campaign_key=campaign_key))
                pv = campaign["param_vector_json"] if "param_vector_json" in campaign.keys() else None  # type: ignore[attr-defined]
                if not pv:
                    flash("No param_vector.json uploaded for this campaign.", "error")
                    return redirect(url_for("campaign_detail", campaign_key=campaign_key))
                # Reconstruct a minimal templates.csv text from DB rows (keeps generator API stable)
                # For MVP, serialize templates back to CSV-like content isn't necessary; we can instead
                # generate a CSV text from stored templates JSON. We'll build it as JSON rows and parse.
                # Simpler: pass templates loaded from DB via a private path is not supported in generator,
                # so we build CSV text here.
                import csv
                import io

                buf = io.StringIO(newline="")
                writer = csv.DictWriter(
                    buf,
                    fieldnames=[
                        "template_id",
                        "vignette_template",
                        "prompt_template",
                        "choices_json",
                        "tags",
                        "rules_yaml",
                    ],
                )
                writer.writeheader()
                for t in templates:
                    writer.writerow(
                        {
                            "template_id": t.template_id,
                            "vignette_template": t.vignette_template,
                            "prompt_template": t.prompt_template,
                            "choices_json": json.dumps(t.choices, ensure_ascii=False),
                            "tags": "|".join(t.tags),
                            # stored as JSON; generator's templates parser treats rules_yaml as YAML, but JSON is valid YAML.
                            "rules_yaml": json.dumps(t.rules, ensure_ascii=False),
                        }
                    )
                templates_csv_text = buf.getvalue()
                param_vector_obj = json.loads(pv)
            else:
                flash(f"Unknown picker_strategy '{picker_strategy}'", "error")
                return redirect(url_for("campaign_detail", campaign_key=campaign_key))

            payload = generate_bulk_payload(
                campaign_key=campaign_key,
                title=str(campaign["title"]),
                questionnaire_version=int(campaign["questionnaire_version"]),
                cases=cases,
                recipients=recipients,
                seed=int(campaign["seed"]),
                picker_strategy=picker_strategy,
                k=k,
                templates_csv_text=templates_csv_text,
                param_vector_obj=param_vector_obj,
            )

            clear_variants_for_campaign(conn, campaign_id=int(campaign["id"]))
            insert_variants(conn, campaign_id=int(campaign["id"]), variants=payload["invitations"])
            # Offline campaigns: also create tokenized invitations + store snapshot so we can email /s/<token>.
            populate_invitations_from_variants(conn, campaign_id=int(campaign["id"]))
            conn.commit()

        flash(f"Generated {len(payload['invitations'])} variants", "success")
        return redirect(url_for("campaign_detail", campaign_key=campaign_key))

    def _stable_tiebreak_int(token: str, item_id: str) -> int:
        h = hashlib.sha256(f"{token}|{item_id}".encode("utf-8")).digest()
        return int.from_bytes(h[:8], "big", signed=False)

    def _assign_on_open(*, conn, campaign_row, invitation_row) -> dict[str, Any]:
        """
        Idempotent:
        - if invitation already has questionnaire_json snapshot, returns it
        - else picks K items with lowest assigned_count (stable tie-break by token),
          stores respondent_assignments + increments question_stats,
          and snapshots questionnaire_json onto the invitation.
        """
        if invitation_row["questionnaire_json"]:
            return json.loads(invitation_row["questionnaire_json"])

        campaign_id = int(campaign_row["id"])
        token = str(invitation_row["token"])
        k = int(campaign_row["k"]) if "k" in campaign_row.keys() else 1  # type: ignore[attr-defined]

        existing = list_assignments_for_token(conn, campaign_id=campaign_id, token=token)
        chosen_item_ids: list[str]
        if existing:
            chosen_item_ids = [str(r["item_id"]) for r in existing]
        else:
            rows = list_question_items_with_stats(conn, campaign_id=campaign_id)
            if not rows:
                raise ValueError("No question_items for campaign; click Generate to build the bank.")
            scored: list[tuple[int, int, str]] = []
            for r in rows:
                assigned_count = int(r["assigned_count"] or 0)
                item_id = str(r["item_id"])
                scored.append((assigned_count, _stable_tiebreak_int(token, item_id), item_id))
            scored.sort(key=lambda t: (t[0], t[1]))
            chosen_item_ids = [t[2] for t in scored[:k]]

            for pos, item_id in enumerate(chosen_item_ids):
                insert_assignment(conn, campaign_id=campaign_id, token=token, item_id=item_id, position=pos)
                increment_assigned_count(conn, campaign_id=campaign_id, item_id=item_id)

        units: list[QuestionUnit] = []
        for item_id in chosen_item_ids:
            qi = get_question_item(conn, campaign_id=campaign_id, item_id=item_id)
            if qi is None:
                raise ValueError(f"Missing question_item '{item_id}'")
            units.append(
                QuestionUnit(
                    vignette_text=str(qi["vignette"]),
                    prompt=str(qi["prompt"]),
                    choices=json.loads(qi["choices_json"]),
                    tags=json.loads(qi["tags_json"]),
                    metadata={"itemId": item_id, "sourceKind": qi["source_kind"], "sourceId": qi["source_id"]},
                )
            )

        qjson = build_questionnaire_from_units(
            title=str(campaign_row["title"]),
            questionnaire_version=int(campaign_row["questionnaire_version"]),
            units=units,
        )
        save_invitation_snapshot(conn, token=token, questionnaire_json_obj=qjson)
        return qjson

    @app.get("/s/<token>")
    def respondent_open(token: str) -> Response:
        """
        Respondent link-open flow:
        - online_assign: assignment happens on first GET
        - offline (pick_k_cases/template_expand): render-only the pre-generated snapshot stored on invitations
        """
        with db.connect() as conn:
            inv = get_invitation_by_token(conn, token=token)
            if inv is None:
                return Response("Invalid token", status=404)
            campaign = conn.execute("SELECT * FROM campaigns WHERE id = ?", (int(inv["campaign_id"]),)).fetchone()
            if campaign is None:
                return Response("Campaign not found", status=404)
            mark_invitation_opened(conn, token=token)
            if campaign["picker_strategy"] == "online_assign":
                qjson = _assign_on_open(conn=conn, campaign_row=campaign, invitation_row=inv)
            else:
                if not inv["questionnaire_json"]:
                    return Response(
                        "No questionnaire snapshot for this token yet. Generate variants for this campaign first.",
                        status=400,
                    )
                qjson = json.loads(inv["questionnaire_json"])
            conn.commit()
        return Response(
            render_template("respondent.html", campaign=campaign, email=inv["email"], token=token, qjson=qjson),
            status=200,
        )

    def _validate_answers_against_snapshot(*, qjson: dict[str, Any], answers: dict[str, str]) -> None:
        """
        Enforces MVP response contract:
        - answers map keys are block ids
        - required answerable blocks (singleSelect, freeText) must be present
        - no unknown block ids
        - singleSelect values must be a valid choice id
        """
        blocks = qjson.get("blocks")
        if not isinstance(blocks, list):
            raise ValueError("Invalid questionnaire snapshot (blocks missing)")

        answerable: dict[str, dict[str, Any]] = {}
        required_ids: set[str] = set()
        for b in blocks:
            if not isinstance(b, dict):
                continue
            btype = b.get("type")
            bid = b.get("id")
            if not isinstance(bid, str) or not bid:
                continue
            if btype in ("singleSelect", "freeText"):
                answerable[bid] = b
                if b.get("required") is True:
                    required_ids.add(bid)

        # unknown keys
        for k in answers.keys():
            if k not in answerable:
                raise ValueError(f"Answer provided for unknown or non-answerable block id '{k}'")

        # missing required
        missing = sorted([rid for rid in required_ids if rid not in answers or not str(answers.get(rid, "")).strip()])
        if missing:
            raise ValueError(f"Missing answers for required blocks: {', '.join(missing)}")

        # validate values
        for bid, val in answers.items():
            b = answerable[bid]
            btype = b.get("type")
            if btype == "freeText":
                if not str(val).strip():
                    raise ValueError(f"freeText '{bid}' must be non-empty")
            elif btype == "singleSelect":
                choices = b.get("choices")
                if not isinstance(choices, list) or not choices:
                    raise ValueError(f"singleSelect '{bid}' has no choices in snapshot")
                allowed = {c.get("id") for c in choices if isinstance(c, dict)}
                if val not in allowed:
                    raise ValueError(f"singleSelect '{bid}' invalid choice '{val}'")
            else:
                raise ValueError(f"Unsupported answerable block type '{btype}'")

    @app.post("/s/<token>/submit")
    def respondent_submit(token: str) -> Response:
        """
        Final submission endpoint (one-and-done):
        - First submit stores answers and returns 200
        - Repeat submit returns 409
        """
        # Collect answers from form POST (MVP UI). Keys are block ids.
        answers: dict[str, str] = {}
        for k, v in request.form.items():
            if k.startswith("ans__"):
                bid = k[len("ans__") :]
                answers[bid] = str(v)

        with db.connect() as conn:
            inv = get_invitation_by_token(conn, token=token)
            if inv is None:
                return Response("Invalid token", status=404)
            campaign = conn.execute("SELECT * FROM campaigns WHERE id = ?", (int(inv["campaign_id"]),)).fetchone()
            if campaign is None:
                return Response("Campaign not found", status=404)
            if campaign["picker_strategy"] != "online_assign":
                return Response("This invitation is not for an online_assign campaign", status=400)

            if has_submission(conn, token=token):
                return Response("Already submitted", status=409)

            if not inv["questionnaire_json"]:
                return Response("Not assigned yet. Open the survey link first.", status=400)

            qjson = json.loads(inv["questionnaire_json"])
            try:
                _validate_answers_against_snapshot(qjson=qjson, answers=answers)
            except Exception as e:
                flash(f"Submit validation error: {e}", "error")
                return redirect(url_for("respondent_open", token=token))

            campaign_id = int(inv["campaign_id"])
            email = str(inv["email"])

            insert_submission(conn, campaign_id=campaign_id, token=token, email=email, answers=answers)

            # Normalize answers for analytics
            blocks = qjson.get("blocks") if isinstance(qjson, dict) else []
            block_by_id: dict[str, dict[str, Any]] = {}
            if isinstance(blocks, list):
                for b in blocks:
                    if isinstance(b, dict) and isinstance(b.get("id"), str):
                        block_by_id[b["id"]] = b

            for bid, val in answers.items():
                b = block_by_id.get(bid) or {}
                btype = str(b.get("type") or "")
                value_text: str | None = None
                value_choice_id: str | None = None
                if btype == "freeText":
                    value_text = str(val)
                elif btype == "singleSelect":
                    value_choice_id = str(val)
                    # Increment submitted_count for the underlying question_item when possible (online_assign only)
                    # We stored item_id in QuestionUnit metadata but not in blocks; instead, use respondent_assignments.
                insert_submission_answer(
                    conn,
                    campaign_id=campaign_id,
                    token=token,
                    block_id=bid,
                    block_type=btype,
                    value_text=value_text,
                    value_choice_id=value_choice_id,
                )

            # Increment submitted_count for each assigned item (online bank items)
            assigned = list_assignments_for_token(conn, campaign_id=campaign_id, token=token)
            for a in assigned:
                increment_submitted_count(conn, campaign_id=campaign_id, item_id=str(a["item_id"]))

            conn.commit()

        flash("Submitted. Thank you!", "success")
        return redirect(url_for("respondent_open", token=token))

    @app.get("/campaigns/<campaign_key>/reports")
    def reports(campaign_key: str) -> str:
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                flash("Campaign not found", "error")
                return redirect(url_for("home"))
            if campaign["picker_strategy"] != "online_assign":
                flash("Reports are currently implemented for online_assign campaigns.", "error")
                return redirect(url_for("campaign_detail", campaign_key=campaign_key))
            campaign_id = int(campaign["id"])
            counts = submission_cohort_counts(conn, campaign_id=campaign_id)
            invited_ns = report_rows(conn, campaign_id=campaign_id, kind="invited_not_submitted")
            opened_ns = report_rows(conn, campaign_id=campaign_id, kind="opened_not_submitted")
            assigned_ns = report_rows(conn, campaign_id=campaign_id, kind="assigned_not_submitted")
            submitted = report_rows(conn, campaign_id=campaign_id, kind="submitted")
        return render_template(
            "reports.html",
            campaign=campaign,
            counts=counts,
            invited_not_submitted=invited_ns,
            opened_not_submitted=opened_ns,
            assigned_not_submitted=assigned_ns,
            submitted=submitted,
        )

    @app.get("/campaigns/<campaign_key>/invitations")
    def campaign_invitations(campaign_key: str) -> str:
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                flash("Campaign not found", "error")
                return redirect(url_for("home"))
            invitations = list_invitations_for_campaign(conn, campaign_id=int(campaign["id"]))
        return render_template("invitations.html", campaign=campaign, invitations=invitations)

    @app.get("/campaigns/<campaign_key>/online-stats")
    def online_stats(campaign_key: str) -> str:
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                flash("Campaign not found", "error")
                return redirect(url_for("home"))
            if campaign["picker_strategy"] != "online_assign":
                flash("This page is only for online_assign campaigns.", "error")
                return redirect(url_for("campaign_detail", campaign_key=campaign_key))

            campaign_id = int(campaign["id"])
            inv_counts = conn.execute(
                """
                SELECT
                  COUNT(*) AS total,
                  SUM(CASE WHEN opened_at IS NOT NULL THEN 1 ELSE 0 END) AS opened,
                  SUM(CASE WHEN questionnaire_hash IS NOT NULL THEN 1 ELSE 0 END) AS assigned
                FROM invitations
                WHERE campaign_id = ?
                """,
                (campaign_id,),
            ).fetchone()
            items = list_question_items_with_stats(conn, campaign_id=campaign_id)
        return render_template("online_stats.html", campaign=campaign, inv_counts=inv_counts, items=items)

    @app.get("/campaigns/<campaign_key>/master")
    def master_view(campaign_key: str) -> str:
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                flash("Campaign not found", "error")
                return redirect(url_for("home"))

            campaign_id = int(campaign["id"])
            cases_n = conn.execute("SELECT COUNT(*) AS n FROM cases").fetchone()["n"]
            recipients_n = conn.execute("SELECT COUNT(*) AS n FROM recipients").fetchone()["n"]
            templates_n = conn.execute("SELECT COUNT(*) AS n FROM templates").fetchone()["n"]
            variants_counts = variant_counts(conn, campaign_id=campaign_id)
            inv_counts = conn.execute(
                """
                SELECT
                  COUNT(*) AS total,
                  SUM(CASE WHEN opened_at IS NOT NULL THEN 1 ELSE 0 END) AS opened,
                  SUM(CASE WHEN questionnaire_hash IS NOT NULL THEN 1 ELSE 0 END) AS assigned
                FROM invitations
                WHERE campaign_id = ?
                """,
                (campaign_id,),
            ).fetchone()
            submit_counts = submission_cohort_counts(conn, campaign_id=campaign_id) if campaign["picker_strategy"] == "online_assign" else None

        return render_template(
            "master.html",
            campaign=campaign,
            cases_n=int(cases_n),
            recipients_n=int(recipients_n),
            templates_n=int(templates_n),
            variants_counts=variants_counts,
            inv_counts=inv_counts,
            submit_counts=submit_counts,
        )

    @app.post("/campaigns/<campaign_key>/email-settings")
    def update_email_settings(campaign_key: str) -> Response:
        email_from = (request.form.get("email_from") or "").strip()
        email_subject = (request.form.get("email_subject") or "").strip()
        email_base_url = (request.form.get("email_base_url") or "").strip()
        email_html = request.form.get("email_html") or ""

        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                flash("Campaign not found", "error")
                return redirect(url_for("home"))
            conn.execute(
                """
                UPDATE campaigns
                SET email_from = ?, email_subject = ?, email_base_url = ?, email_html = ?
                WHERE campaign_key = ?
                """,
                (email_from, email_subject, email_base_url, email_html, campaign_key),
            )
            conn.commit()

        flash("Saved email settings", "success")
        return redirect(url_for("master_view", campaign_key=campaign_key))

    @app.post("/campaigns/<campaign_key>/send-emails")
    def send_emails(campaign_key: str) -> Response:
        """
        Sends invitation emails for both online_assign and offline campaigns using a per-campaign Resend template.
        HARD SAFETY: all outbound emails are forced to kohane@gmail.com (see resend_client.FORCED_TEST_TO_EMAIL).
        """
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                flash("Campaign not found", "error")
                return redirect(url_for("home"))

            # Ensure invitations exist
            inv_rows = list_invitations_for_campaign(conn, campaign_id=int(campaign["id"]))
            if not inv_rows:
                flash("No invitations yet. Click Generate/Prepare first.", "error")
                return redirect(url_for("master_view", campaign_key=campaign_key))

            # Offline campaigns require snapshot to be present for tokenized links
            if campaign["picker_strategy"] != "online_assign":
                missing = [r for r in inv_rows if not r["questionnaire_json"]]
                if missing:
                    flash("Some invitations are missing questionnaire snapshots. Click Generate first.", "error")
                    return redirect(url_for("master_view", campaign_key=campaign_key))

            email_from = (campaign["email_from"] or "").strip()
            email_subject = (campaign["email_subject"] or "").strip()
            email_html = (campaign["email_html"] or "").strip()
            base_url = (campaign["email_base_url"] or "http://127.0.0.1:5055").strip()

            if not email_from or not email_subject or not email_html:
                flash("Missing email settings: email_from, email_subject, and email_html are required.", "error")
                return redirect(url_for("master_view", campaign_key=campaign_key))

            # Create/update template
            try:
                template_id = create_or_update_campaign_template(
                    campaign_key=campaign_key,
                    template_id=(campaign["email_template_id"] or None),
                    from_email=email_from,
                    subject=email_subject,
                    html=email_html,
                )
            except ResendError as e:
                flash(f"Resend error creating/updating template: {e}", "error")
                return redirect(url_for("master_view", campaign_key=campaign_key))

            # Persist template_id back onto campaign
            conn.execute(
                "UPDATE campaigns SET email_template_id = ? WHERE campaign_key = ?",
                (template_id, campaign_key),
            )
            conn.commit()

        # Send outside transaction (still safe; recipients are forced to kohane@gmail.com)
        try:
            sends = send_invites_for_campaign(
                template_id=template_id,
                campaign_title=str(campaign["title"]),
                base_url=base_url,
                invitations=[{"email": r["email"], "token": r["token"]} for r in inv_rows],
            )
        except ResendError as e:
            flash(f"Resend send error: {e}", "error")
            return redirect(url_for("master_view", campaign_key=campaign_key))

        flash(f"Sent {len(sends)} emails (forced delivery to kohane@gmail.com).", "success")
        return redirect(url_for("master_view", campaign_key=campaign_key))

    @app.get("/campaigns/<campaign_key>/export_invitations.json")
    def export_invitations_json(campaign_key: str) -> Response:
        """
        Online mode export: list of invitations (email + token).
        """
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                return Response("Campaign not found", status=404)
            if campaign["picker_strategy"] != "online_assign":
                return Response("Not an online_assign campaign", status=400)
            rows = list_invitations_for_campaign(conn, campaign_id=int(campaign["id"]))

        invitations = [{"email": r["email"], "token": r["token"]} for r in rows]
        payload = {"campaignKey": campaign_key, "invitations": invitations}
        body = json.dumps(payload, indent=2, ensure_ascii=False)
        return Response(
            body,
            status=200,
            mimetype="application/json",
            headers={"Content-Disposition": f'attachment; filename="{campaign_key}.invitations.json"'},
        )

    @app.get("/campaigns/<campaign_key>/preview")
    def preview_first(campaign_key: str) -> str:
        # Simple preview: render the first recipient's variant JSON in a safe HTML view.
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                flash("Campaign not found", "error")
                return redirect(url_for("home"))
            rows = conn.execute(
                "SELECT * FROM invitation_variants WHERE campaign_id = ? ORDER BY email LIMIT 1",
                (int(campaign["id"]),),
            ).fetchall()
            if not rows:
                flash("No generated variants yet. Click Generate first.", "error")
                return redirect(url_for("campaign_detail", campaign_key=campaign_key))
            row = rows[0]
            qjson = json.loads(row["questionnaire_json"])
        return render_template("preview.html", campaign=campaign, email=row["email"], qjson=qjson)

    @app.get("/campaigns/<campaign_key>/stats")
    def stats(campaign_key: str) -> str:
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                flash("Campaign not found", "error")
                return redirect(url_for("home"))
            counts = variant_counts(conn, campaign_id=int(campaign["id"]))
            top = conn.execute(
                """
                SELECT
                  questionnaire_hash,
                  COUNT(*) as n,
                  MIN(email) as example_email,
                  MIN(case_id) as example_case_id
                FROM invitation_variants
                WHERE campaign_id = ?
                GROUP BY questionnaire_hash
                ORDER BY n DESC, questionnaire_hash
                LIMIT 200
                """,
                (int(campaign["id"]),),
            ).fetchall()
        return render_template("stats.html", campaign=campaign, counts=counts, top=top)

    @app.get("/campaigns/<campaign_key>/hash/<questionnaire_hash>")
    def hash_detail(campaign_key: str, questionnaire_hash: str) -> str:
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                flash("Campaign not found", "error")
                return redirect(url_for("home"))

            rows = conn.execute(
                """
                SELECT email, case_id, created_at
                FROM invitation_variants
                WHERE campaign_id = ? AND questionnaire_hash = ?
                ORDER BY email
                """,
                (int(campaign["id"]), questionnaire_hash),
            ).fetchall()

            if not rows:
                flash("No rows found for that hash", "error")
                return redirect(url_for("stats", campaign_key=campaign_key))

            qrow = conn.execute(
                """
                SELECT questionnaire_json
                FROM invitation_variants
                WHERE campaign_id = ? AND questionnaire_hash = ?
                LIMIT 1
                """,
                (int(campaign["id"]), questionnaire_hash),
            ).fetchone()

            questionnaire_json = json.loads(qrow["questionnaire_json"]) if qrow else None

        return render_template(
            "hash_detail.html",
            campaign=campaign,
            questionnaire_hash=questionnaire_hash,
            rows=rows,
            questionnaire_json=questionnaire_json,
        )

    @app.get("/campaigns/<campaign_key>/export.json")
    def export_bulk_json(campaign_key: str) -> Response:
        """
        Download the bulk invitations JSON payload (ready for later Cloudflare upload).
        Requires that variants have been generated for the campaign.
        """
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                return Response("Campaign not found", status=404)
            if campaign["picker_strategy"] == "online_assign":
                return Response(
                    "This campaign is online_assign. Use /export_invitations.json (tokens) and open /s/<token> to snapshot.",
                    status=400,
                )
            rows = conn.execute(
                """
                SELECT email, questionnaire_json, metadata_json
                FROM invitation_variants
                WHERE campaign_id = ?
                ORDER BY email
                """,
                (int(campaign["id"]),),
            ).fetchall()

        invitations: list[dict[str, Any]] = []
        for r in rows:
            invitations.append(
                {
                    "email": r["email"],
                    "questionnaireVersion": int(campaign["questionnaire_version"]),
                    "questionnaireJson": json.loads(r["questionnaire_json"]),
                    "metadata": json.loads(r["metadata_json"]),
                }
            )

        payload = {"campaignKey": campaign_key, "invitations": invitations}
        body = json.dumps(payload, indent=2, ensure_ascii=False)
        return Response(
            body,
            status=200,
            mimetype="application/json",
            headers={"Content-Disposition": f'attachment; filename="{campaign_key}.bulk_invitations.json"'},
        )

    return app


def _main() -> None:
    app = create_app()
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5055")), debug=True)


if __name__ == "__main__":
    _main()


