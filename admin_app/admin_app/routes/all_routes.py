from __future__ import annotations

import csv
import io
import json
import os
import secrets
import sqlite3
import hashlib
from typing import Any

from flask import Flask, Response, flash, redirect, render_template, request, url_for

from qgen.generator import generate_bulk_payload
from qgen.io_csv import parse_cases_csv, parse_recipients_csv
from qgen.io_templates_csv import parse_templates_csv
from qgen.templates_contracts import parse_param_vector_json

from ..db import (
    DEFAULT_LAYOUT_YAML,
    Db,
    clear_question_bank,
    clear_variants_for_campaign,
    count_pending_recipients,
    create_invitations_for_campaign,
    exclude_recipient_from_campaign,
    get_campaign_by_key,
    get_invitation_by_token,
    get_next_wave_number,
    get_recipients_with_variants,
    has_submission,
    increment_submitted_count,
    insert_cloud_push,
    insert_cloud_push_tokens,
    insert_generation_wave,
    insert_submission,
    insert_submission_answer,
    insert_variants,
    list_assignments_for_token,
    list_campaigns,
    list_cloud_invitation_ledger_rows,
    list_cloud_latest_tokens,
    list_cloud_pushes,
    list_cloud_recent_submissions,
    list_cloud_tokens_for_push,
    list_events,
    list_excluded_recipients_for_campaign,
    list_free_text_answers,
    list_generation_waves,
    list_invitations_for_campaign,
    list_invitation_ledger_rows,
    list_pending_recipients_for_campaign,
    list_question_items_with_stats,
    list_recent_submissions,
    list_submissions_with_answers,
    load_cases,
    load_recipients,
    load_templates,
    mark_invitation_opened,
    populate_invitations_from_variants,
    report_rows,
    restore_recipient_to_campaign,
    set_setting,
    single_select_choice_counts,
    submission_cohort_counts,
    update_campaign_email_yaml,
    update_campaign_layout_yaml,
    upsert_campaign,
    upsert_cases,
    upsert_question_items_from_cases,
    upsert_recipients,
    upsert_templates,
    variant_counts,
)
from ..logic import (
    DEFAULT_EMAIL_YAML,
    admin_mode_from_conn,
    assign_on_open,
    csv_escape,
    log_event,
    maybe_sync_cloud_submissions,
    normalize_email_config,
    normalize_layout_config,
    recipient_name_map,
    validate_answers_against_snapshot,
)
from ..resend_client import ResendError, create_or_update_campaign_template, send_invites_for_campaign, _html_to_plain_text
from ..utils import (
    canonical_json_bytes,
    cloud_post_json,
    email_config_to_yaml,
    get_cloud_config,
    get_railway_config,
    parse_json_obj,
    render_email_preview,
)


def register(app: Flask, db: Db) -> None:
    # -----------------------------
    # Home + settings
    # -----------------------------
    @app.get("/")
    def home() -> str:
        with db.connect() as conn:
            campaigns = list_campaigns(conn)
            templates_count = int(conn.execute("SELECT COUNT(*) AS n FROM templates").fetchone()["n"])
            cases_count = int(conn.execute("SELECT COUNT(*) AS n FROM cases").fetchone()["n"])
            recipients_count = int(conn.execute("SELECT COUNT(*) AS n FROM recipients").fetchone()["n"])

            templates_last_updated = conn.execute("SELECT MAX(updated_at) AS v FROM templates").fetchone()["v"]
            cases_last_updated = conn.execute("SELECT MAX(updated_at) AS v FROM cases").fetchone()["v"]
            recipients_last_updated = conn.execute("SELECT MAX(updated_at) AS v FROM recipients").fetchone()["v"]

            mode = admin_mode_from_conn(conn)
        return render_template(
            "home.html",
            campaigns=campaigns,
            db_path=str(db.db_path),
            templates_count=templates_count,
            cases_count=cases_count,
            recipients_count=recipients_count,
            templates_last_updated=templates_last_updated,
            cases_last_updated=cases_last_updated,
            recipients_last_updated=recipients_last_updated,
            admin_mode=mode,
        )

    @app.post("/settings/mode")
    def update_admin_mode() -> Response:
        mode = (request.form.get("admin_mode") or "").strip().lower()
        if mode not in ("local", "cloud"):
            flash("admin_mode must be local or cloud", "error")
            return redirect(request.referrer or url_for("home"))
        with db.connect() as conn:
            set_setting(conn, key="admin_mode", value=mode)
            conn.commit()
        flash(f"Admin mode set to {mode}", "success")
        return redirect(request.referrer or url_for("home"))

    # -----------------------------
    # Campaign CRUD + imports
    # -----------------------------
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

        if picker_strategy not in ("pick_k_cases", "template_expand", "online_assign"):
            flash("picker_strategy must be pick_k_cases, template_expand, or online_assign", "error")
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
                picker_strategy=picker_strategy,
                k=k,
            )
            conn.commit()
        flash(f"Created campaign '{campaign_key}' with strategy '{picker_strategy}' and k={k}.", "success")
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
        campaign_key = request.form.get("campaign_key")  # Optional: track which campaign triggered this
        if not f:
            flash("Please choose a cases.csv file to upload", "error")
            if campaign_key:
                return redirect(url_for("master_view", campaign_key=campaign_key))
            return redirect(request.referrer or url_for("home"))
        replace_existing = (request.form.get("replace_existing") or "0").strip() == "1"
        try:
            text = f.stream.read().decode("utf-8")
            cases = parse_cases_csv(text)
        except Exception as e:
            with db.connect() as conn:
                log_event(conn, campaign=None, event="import_cases", success=False, message=str(e))
            flash(f"Failed to parse cases.csv: {e}", "error")
            if campaign_key:
                return redirect(url_for("master_view", campaign_key=campaign_key))
            return redirect(request.referrer or url_for("home"))

        with db.connect() as conn:
            old_total = int(conn.execute("SELECT COUNT(*) AS n FROM cases").fetchone()["n"])
            if replace_existing:
                conn.execute("DELETE FROM cases")
            n = upsert_cases(conn, cases)
            new_total = int(conn.execute("SELECT COUNT(*) AS n FROM cases").fetchone()["n"])
            conn.commit()
            mode = "replaced" if replace_existing else "upserted"
            log_event(
                conn,
                campaign=None,
                event="import_cases",
                success=True,
                message=f"{mode} {n} cases (total now {new_total}, was {old_total})",
            )
        flash(
            f"Imported {n} cases ({'replaced existing' if replace_existing else 'merged into existing'}). Total cases now {new_total}.",
            "success",
        )
        if campaign_key:
            return redirect(url_for("master_view", campaign_key=campaign_key))
        return redirect(request.referrer or url_for("home"))

    @app.post("/imports/recipients")
    def import_recipients() -> Response:
        f = request.files.get("file")
        campaign_key = request.form.get("campaign_key")  # Optional: track which campaign triggered this
        if not f:
            flash("Please choose a recipients.csv file to upload", "error")
            if campaign_key:
                return redirect(url_for("master_view", campaign_key=campaign_key))
            return redirect(request.referrer or url_for("home"))
        replace_existing = (request.form.get("replace_existing") or "0").strip() == "1"
        try:
            text = f.stream.read().decode("utf-8")
            recs = parse_recipients_csv(text)
        except Exception as e:
            with db.connect() as conn:
                log_event(conn, campaign=None, event="import_recipients", success=False, message=str(e))
            flash(f"Failed to parse recipients.csv: {e}", "error")
            if campaign_key:
                return redirect(url_for("master_view", campaign_key=campaign_key))
            return redirect(request.referrer or url_for("home"))

        with db.connect() as conn:
            old_total = int(conn.execute("SELECT COUNT(*) AS n FROM recipients").fetchone()["n"])
            if replace_existing:
                conn.execute("DELETE FROM recipients")
            n = upsert_recipients(conn, recs)
            new_total = int(conn.execute("SELECT COUNT(*) AS n FROM recipients").fetchone()["n"])
            conn.commit()
            mode = "replaced" if replace_existing else "upserted"
            log_event(
                conn,
                campaign=None,
                event="import_recipients",
                success=True,
                message=f"{mode} {n} recipients (total now {new_total}, was {old_total})",
            )
        flash(
            f"Imported {n} recipients ({'replaced existing' if replace_existing else 'merged into existing'}). Total recipients now {new_total}.",
            "success",
        )
        if campaign_key:
            return redirect(url_for("master_view", campaign_key=campaign_key))
        return redirect(request.referrer or url_for("home"))

    @app.post("/imports/templates")
    def import_templates() -> Response:
        f = request.files.get("file")
        campaign_key = request.form.get("campaign_key")  # Optional: track which campaign triggered this
        if not f:
            flash("Please choose a templates.csv file to upload", "error")
            if campaign_key:
                return redirect(url_for("master_view", campaign_key=campaign_key))
            return redirect(request.referrer or url_for("home"))
        try:
            text = f.stream.read().decode("utf-8")
            templates = parse_templates_csv(text)
        except Exception as e:
            with db.connect() as conn:
                log_event(conn, campaign=None, event="import_templates", success=False, message=str(e))
            flash(f"Failed to parse templates.csv: {e}", "error")
            if campaign_key:
                return redirect(url_for("master_view", campaign_key=campaign_key))
            return redirect(request.referrer or url_for("home"))

        with db.connect() as conn:
            n = upsert_templates(conn, templates)
            conn.commit()
            log_event(conn, campaign=None, event="import_templates", success=True, message=f"Imported {n} templates")
        flash(f"Imported {n} templates", "success")
        if campaign_key:
            return redirect(url_for("master_view", campaign_key=campaign_key))
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
                flash("No recipients imported yet. Import recipients.csv first in Master page.", "error")
                log_event(conn, campaign=campaign, event="generate_variants", success=False, message="No recipients imported yet")
                return redirect(url_for("master_view", campaign_key=campaign_key))

            # Read campaign settings - use try/except for robust column access
            try:
                picker_strategy = campaign["picker_strategy"] or "pick_k_cases"
            except (KeyError, IndexError):
                picker_strategy = "pick_k_cases"
            
            try:
                k = int(campaign["k"]) if campaign["k"] else 1
            except (KeyError, IndexError, TypeError):
                k = 1
            
            seed = int(campaign["seed"])
            campaign_id = int(campaign["id"])

            # Get next wave number
            wave_number = get_next_wave_number(conn, campaign_id=campaign_id)
            
            # Get recipients who already have variants (additive generation)
            existing_recipient_emails = get_recipients_with_variants(conn, campaign_id=campaign_id)
            
            # Filter to only new recipients
            new_recipients = [r for r in recipients if r.email.lower().strip() not in existing_recipient_emails]
            
            if not new_recipients:
                flash("All recipients already have variants. Import new recipients to generate more.", "info")
                return redirect(url_for("master_view", campaign_key=campaign_key))

            if picker_strategy == "online_assign":
                if not cases:
                    flash("No cases imported yet. Import cases.csv first in Master page.", "error")
                    log_event(conn, campaign=campaign, event="generate_variants", success=False, message="No cases imported yet")
                    return redirect(url_for("master_view", campaign_key=campaign_key))
                
                # For online_assign, only build question bank once (first wave)
                if wave_number == 1:
                    clear_question_bank(conn, campaign_id=campaign_id)
                    n_items = upsert_question_items_from_cases(conn, campaign_id=campaign_id, cases=cases)
                else:
                    n_items = len(list_question_items_with_stats(conn, campaign_id=campaign_id))
                
                # Create invitations for new recipients only
                n_inv = create_invitations_for_campaign(conn, campaign_id=campaign_id, recipients=new_recipients)
                
                # Record wave
                wave_id = insert_generation_wave(
                    conn,
                    campaign_id=campaign_id,
                    wave_number=wave_number,
                    picker_strategy=picker_strategy,
                    k=k,
                    seed=seed,
                    recipients_processed=len(new_recipients),
                    variants_created=n_inv,
                )
                
                conn.commit()
                flash(f"Wave {wave_number}: Created {n_inv} new invitations ({n_items} question items in bank)", "success")
                log_event(conn, campaign=campaign, event="generate_variants", success=True, message=f"Wave {wave_number}: {n_inv} invitations")
                return redirect(url_for("master_view", campaign_key=campaign_key))

            # Offline strategies: pick_k_cases and template_expand
            templates_csv_text: str | None = None
            param_vector_obj: dict[str, Any] | None = None

            if picker_strategy == "pick_k_cases":
                if not cases:
                    flash("No cases imported yet. Import cases.csv first in Master page.", "error")
                    log_event(conn, campaign=campaign, event="generate_variants", success=False, message="No cases imported yet")
                    return redirect(url_for("master_view", campaign_key=campaign_key))
            elif picker_strategy == "template_expand":
                if not templates:
                    flash("No templates imported yet. Import templates.csv first in Master page.", "error")
                    log_event(conn, campaign=campaign, event="generate_variants", success=False, message="No templates imported yet")
                    return redirect(url_for("master_view", campaign_key=campaign_key))
                pv = campaign["param_vector_json"] if "param_vector_json" in campaign else None
                if not pv:
                    flash("No param_vector.json uploaded for this campaign.", "error")
                    log_event(conn, campaign=campaign, event="generate_variants", success=False, message="Missing param_vector")
                    return redirect(url_for("master_view", campaign_key=campaign_key))

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
                            "rules_yaml": json.dumps(t.rules, ensure_ascii=False),
                        }
                    )
                templates_csv_text = buf.getvalue()
                param_vector_obj = json.loads(pv)
            else:
                flash(f"Unknown picker_strategy '{picker_strategy}'", "error")
                return redirect(url_for("master_view", campaign_key=campaign_key))

            # Generate variants for NEW recipients only
            payload = generate_bulk_payload(
                campaign_key=campaign_key,
                title=str(campaign["title"]),
                questionnaire_version=int(campaign["questionnaire_version"]),
                cases=cases,
                recipients=new_recipients,  # Only new recipients
                seed=seed,
                picker_strategy=picker_strategy,
                k=k,
                templates_csv_text=templates_csv_text,
                param_vector_obj=param_vector_obj,
            )

            # Insert new variants with wave_id (additive, don't clear existing)
            wave_id = insert_generation_wave(
                conn,
                campaign_id=campaign_id,
                wave_number=wave_number,
                picker_strategy=picker_strategy,
                k=k,
                seed=seed,
                recipients_processed=len(new_recipients),
                variants_created=len(payload["invitations"]),
            )
            
            insert_variants(conn, campaign_id=campaign_id, variants=payload["invitations"], wave_id=wave_id)
            populate_invitations_from_variants(conn, campaign_id=campaign_id)
            conn.commit()
            
            log_event(
                conn,
                campaign=campaign,
                event="generate_variants",
                success=True,
                message=f"Wave {wave_number}: Generated {len(payload['invitations'])} variants for {len(new_recipients)} new recipients",
            )
        flash(f"Wave {wave_number}: Generated {len(payload['invitations'])} variants for {len(new_recipients)} new recipients", "success")
        return redirect(url_for("master_view", campaign_key=campaign_key))

    # -----------------------------
    # Respondent flow
    # -----------------------------
    @app.get("/s/<token>")
    def respondent_open(token: str) -> Response:
        with db.connect() as conn:
            inv = get_invitation_by_token(conn, token=token)
            if inv is None:
                return Response("Invalid token", status=404)
            campaign = conn.execute("SELECT * FROM campaigns WHERE id = ?", (int(inv["campaign_id"]),)).fetchone()
            if campaign is None:
                return Response("Campaign not found", status=404)
            mark_invitation_opened(conn, token=token)
            if campaign["picker_strategy"] == "online_assign":
                qjson = assign_on_open(conn=conn, campaign_row=campaign, invitation_row=inv)
            else:
                if not inv["questionnaire_json"]:
                    return Response(
                        "No questionnaire snapshot for this token yet. Generate variants for this campaign first.",
                        status=400,
                    )
                qjson = json.loads(inv["questionnaire_json"])
            conn.commit()
        return Response(
            render_template(
                "respondent.html",
                campaign=campaign,
                email=inv["email"],
                token=token,
                qjson=qjson,
                layout_config=normalize_layout_config(str(campaign["layout_yaml"] or DEFAULT_LAYOUT_YAML)),
            ),
            status=200,
        )

    @app.post("/s/<token>/submit")
    def respondent_submit(token: str) -> Response:
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

            if has_submission(conn, token=token):
                return Response("Already submitted", status=409)

            if not inv["questionnaire_json"]:
                return Response("Not assigned yet. Open the survey link first.", status=400)

            qjson = json.loads(inv["questionnaire_json"])
            try:
                validate_answers_against_snapshot(qjson=qjson, answers=answers)
            except Exception as e:
                flash(f"Submit validation error: {e}", "error")
                return redirect(url_for("respondent_open", token=token))

            campaign_id = int(inv["campaign_id"])
            email = str(inv["email"])

            insert_submission(conn, campaign_id=campaign_id, token=token, email=email, answers=answers)

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
                insert_submission_answer(
                    conn,
                    campaign_id=campaign_id,
                    token=token,
                    block_id=bid,
                    block_type=btype,
                    value_text=value_text,
                    value_choice_id=value_choice_id,
                )

            if campaign["picker_strategy"] == "online_assign":
                assigned = list_assignments_for_token(conn, campaign_id=campaign_id, token=token)
                for a in assigned:
                    increment_submitted_count(conn, campaign_id=campaign_id, item_id=str(a["item_id"]))

            conn.commit()

        flash("Submitted. Thank you!", "success")
        return redirect(url_for("respondent_open", token=token))

    # -----------------------------
    # Admin views (reports/results/master/etc)
    # -----------------------------
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

    @app.get("/campaigns/<campaign_key>/results")
    def results(campaign_key: str) -> str:
        cloud_base_url, cloud_admin_token = get_cloud_config()
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                flash("Campaign not found", "error")
                return redirect(url_for("home"))
            campaign_id = int(campaign["id"])
            mode = admin_mode_from_conn(conn)

            if mode == "cloud" and cloud_base_url and cloud_admin_token:
                try:
                    sync_status = maybe_sync_cloud_submissions(
                        conn=conn,
                        campaign=campaign,
                        cloud_base_url=cloud_base_url,
                        cloud_admin_token=cloud_admin_token,
                        force=False,
                    )
                    if sync_status.get("did_sync"):
                        conn.commit()
                except Exception:
                    pass

            counts = submission_cohort_counts(conn, campaign_id=campaign_id)
            ss_counts = single_select_choice_counts(conn, campaign_id=campaign_id)
            ft = list_free_text_answers(conn, campaign_id=campaign_id, limit=500)
        return render_template(
            "results.html",
            campaign=campaign,
            admin_mode=mode,
            counts=counts,
            single_select_counts=ss_counts,
            free_text_answers=ft,
        )

    @app.get("/campaigns/<campaign_key>/submissions")
    def submissions_detail(campaign_key: str) -> str:
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                flash("Campaign not found", "error")
                return redirect(url_for("home"))
            campaign_id = int(campaign["id"])

            submissions_raw = list_submissions_with_answers(conn, campaign_id=campaign_id)

            submissions = []
            for row in submissions_raw:
                strata = parse_json_obj(row["strata_json"] if "strata_json" in row else None)
                answers_obj = parse_json_obj(row["answers_json"] if "answers_json" in row else None)
                answers_parsed = answers_obj.get("answers", answers_obj) if isinstance(answers_obj, dict) else {}
                submissions.append(
                    {
                        "email": row["email"],
                        "firstname": strata.get("firstname", ""),
                        "lastname": strata.get("lastname", ""),
                        "token": row["token"],
                        "submitted_at": row["submitted_at"],
                        "answers": answers_parsed,
                    }
                )

        return render_template(
            "submissions.html",
            campaign=campaign,
            submissions=submissions,
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
        cloud_base_url, cloud_admin_token = get_cloud_config()
        railway_app_url, _ = get_railway_config()
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                flash("Campaign not found", "error")
                return redirect(url_for("home"))

            campaign_id = int(campaign["id"])
            mode = admin_mode_from_conn(conn)
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

            cloud_last_upload = None
            cloud_latest_tokens = []
            cloud_push_history: list[dict[str, Any]] = []
            cloud_sync_status: dict[str, Any] | None = None
            cloud_sync_error: str | None = None
            events: list[Any] = list_events(conn, campaign_id=campaign_id, limit=20)
            if cloud_base_url:
                cloud_last_upload = conn.execute(
                    """
                    SELECT created_at, request_hash, id AS push_id
                    FROM cloud_pushes
                    WHERE campaign_id = ? AND cloud_base_url = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (campaign_id, cloud_base_url),
                ).fetchone()
                cloud_latest_tokens = list_cloud_latest_tokens(conn, campaign_id=campaign_id, cloud_base_url=cloud_base_url)
                pushes = list_cloud_pushes(conn, campaign_id=campaign_id, cloud_base_url=cloud_base_url)
                for p in pushes:
                    cloud_push_history.append(
                        {
                            "push": p,
                            "tokens": list_cloud_tokens_for_push(conn, push_id=int(p["push_id"])),
                        }
                    )

                if mode == "cloud" and cloud_admin_token:
                    try:
                        cloud_sync_status = maybe_sync_cloud_submissions(
                            conn=conn,
                            campaign=campaign,
                            cloud_base_url=cloud_base_url,
                            cloud_admin_token=cloud_admin_token,
                            force=False,
                        )
                        if cloud_sync_status.get("did_sync"):
                            conn.commit()
                    except Exception as e:
                        cloud_sync_error = str(e)

            cohort_counts = submission_cohort_counts(conn, campaign_id=campaign_id)
            recipient_counts = count_pending_recipients(conn, campaign_id=campaign_id)
            if mode == "cloud" and cloud_base_url and campaign["picker_strategy"] != "online_assign":
                recent_submissions = list_cloud_recent_submissions(conn, campaign_id=campaign_id, cloud_base_url=cloud_base_url, limit=20)
                ledger_rows = list_cloud_invitation_ledger_rows(conn, campaign_id=campaign_id, cloud_base_url=cloud_base_url)
            else:
                recent_submissions = list_recent_submissions(conn, campaign_id=campaign_id, limit=20)
                ledger_rows = list_invitation_ledger_rows(conn, campaign_id=campaign_id)

            # Wave tracking data
            generation_waves = list_generation_waves(conn, campaign_id=campaign_id)
            existing_recipient_emails = get_recipients_with_variants(conn, campaign_id=campaign_id)
            total_recipients = int(recipients_n)
            recipients_with_variants = len(existing_recipient_emails)
            pending_recipients = total_recipients - recipients_with_variants

        existing_email_yaml = campaign["email_yaml"] if "email_yaml" in campaign else None
        if not existing_email_yaml and (campaign["email_from"] or campaign["email_subject"] or campaign["email_html"]):
            existing_email_yaml = email_config_to_yaml(
                from_email=campaign["email_from"] or "",
                subject=campaign["email_subject"] or "",
                base_url=campaign["email_base_url"] or "http://127.0.0.1:5055",
                html=campaign["email_html"] or "",
            )
        email_yaml_display = existing_email_yaml or DEFAULT_EMAIL_YAML

        return render_template(
            "master.html",
            campaign=campaign,
            admin_mode=mode,
            layout_yaml=str(campaign["layout_yaml"] or DEFAULT_LAYOUT_YAML),
            layout_config=normalize_layout_config(str(campaign["layout_yaml"] or DEFAULT_LAYOUT_YAML)),
            email_yaml=email_yaml_display,
            email_config=normalize_email_config(email_yaml_display),
            cases_n=int(cases_n),
            recipients_n=int(recipients_n),
            templates_n=int(templates_n),
            variants_counts=variants_counts,
            inv_counts=inv_counts,
            cohort_counts=cohort_counts,
            recipient_counts=recipient_counts,
            recent_submissions=recent_submissions,
            ledger_rows=ledger_rows,
            cloud_base_url=cloud_base_url,
            cloud_last_upload=cloud_last_upload,
            cloud_latest_tokens=cloud_latest_tokens,
            cloud_push_history=cloud_push_history,
            cloud_sync_status=cloud_sync_status,
            cloud_sync_error=cloud_sync_error,
            cloud_admin_token_present=bool(cloud_admin_token),
            railway_app_url=railway_app_url,
            event_log=events,
            generation_waves=generation_waves,
            recipients_with_variants=recipients_with_variants,
            pending_recipients=pending_recipients,
        )

    @app.post("/campaigns/<campaign_key>/cloud/sync")
    def cloud_sync_now(campaign_key: str) -> Response:
        cloud_base_url, cloud_admin_token = get_cloud_config()
        if not cloud_base_url or not cloud_admin_token:
            flash("Missing env vars: CLOUDFLARE_STUDY_BASE_URL and CLOUDFLARE_ADMIN_TOKEN are required.", "error")
            return redirect(url_for("master_view", campaign_key=campaign_key))
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                flash("Campaign not found", "error")
                return redirect(url_for("home"))
            try:
                status = maybe_sync_cloud_submissions(
                    conn=conn,
                    campaign=campaign,
                    cloud_base_url=cloud_base_url,
                    cloud_admin_token=cloud_admin_token,
                    force=True,
                )
                conn.commit()
                flash(
                    f"Synced from Cloudflare: {status.get('local_submissions_upserted', 0)} submissions, {status.get('local_answers_written', 0)} answers",
                    "success",
                )
            except Exception as e:
                flash(f"Cloud sync failed: {e}", "error")
        return redirect(url_for("master_view", campaign_key=campaign_key))

    @app.post("/campaigns/<campaign_key>/layout-yaml")
    def update_layout_yaml(campaign_key: str) -> Response:
        layout_yaml = request.form.get("layout_yaml") or ""
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                flash("Campaign not found", "error")
                return redirect(url_for("home"))
            try:
                normalize_layout_config(layout_yaml)
            except Exception as e:
                flash(f"Invalid layout YAML: {e}", "error")
                return redirect(url_for("master_view", campaign_key=campaign_key))
            update_campaign_layout_yaml(conn, campaign_key=campaign_key, layout_yaml=layout_yaml)
            conn.commit()
        flash("Saved layout YAML", "success")
        return redirect(url_for("master_view", campaign_key=campaign_key))

    @app.post("/campaigns/<campaign_key>/email-yaml")
    def update_email_yaml(campaign_key: str) -> Response:
        email_yaml = request.form.get("email_yaml") or ""
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                flash("Campaign not found", "error")
                return redirect(url_for("home"))
            try:
                config = normalize_email_config(email_yaml)
                if not config["from_email"] or not config["subject"] or not config["html"]:
                    raise ValueError("from, subject, and html are required")
            except Exception as e:
                flash(f"Invalid email YAML: {e}", "error")
                return redirect(url_for("master_view", campaign_key=campaign_key))
            update_campaign_email_yaml(conn, campaign_key=campaign_key, email_yaml=email_yaml)
            conn.execute(
                """
                UPDATE campaigns
                SET email_from = ?, email_subject = ?, email_base_url = ?, email_html = ?
                WHERE campaign_key = ?
                """,
                (config["from_email"], config["subject"], config["base_url"], config["html"], campaign_key),
            )
            log_event(conn, campaign=campaign, event="update_email_yaml", success=True)
            conn.commit()
        flash("Saved email YAML (and synced to individual fields)", "success")
        return redirect(url_for("master_view", campaign_key=campaign_key))

    @app.post("/campaigns/<campaign_key>/cloud/push")
    def cloud_push(campaign_key: str) -> Response:
        cloud_base_url, cloud_admin_token = get_cloud_config()
        if not cloud_base_url or not cloud_admin_token:
            flash("Missing env vars: CLOUDFLARE_STUDY_BASE_URL and CLOUDFLARE_ADMIN_TOKEN are required.", "error")
            return redirect(url_for("master_view", campaign_key=campaign_key))

        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                flash("Campaign not found", "error")
                return redirect(url_for("home"))
            if campaign["picker_strategy"] == "online_assign":
                flash("Cloud push currently supports offline campaigns (pick_k_cases/template_expand) only.", "error")
                return redirect(url_for("master_view", campaign_key=campaign_key))

            rows = conn.execute(
                """
                SELECT email, questionnaire_json, metadata_json
                FROM invitation_variants
                WHERE campaign_id = ?
                ORDER BY email
                """,
                (int(campaign["id"]),),
            ).fetchall()
            if not rows:
                flash("No generated variants yet. Click Generate variants first.", "error")
                return redirect(url_for("master_view", campaign_key=campaign_key))

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
            payload = {"campaignKey": campaign_key, "layoutConfig": normalize_layout_config(str(campaign["layout_yaml"] or DEFAULT_LAYOUT_YAML)), "invitations": invitations}
            request_hash = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()

        try:
            resp_obj = cloud_post_json(
                url=f"{cloud_base_url}/api/admin/upload",
                bearer_token=cloud_admin_token,
                payload_obj=payload,
            )
        except Exception as e:
            flash(f"Cloud push failed: {e}", "error")
            return redirect(url_for("master_view", campaign_key=campaign_key))

        tokens = resp_obj.get("invitations")
        if not isinstance(tokens, list):
            flash(f"Cloud push returned unexpected response: {str(resp_obj)[:300]}", "error")
            return redirect(url_for("master_view", campaign_key=campaign_key))

        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            assert campaign is not None
            campaign_id = int(campaign["id"])
            response_json = json.dumps(resp_obj, ensure_ascii=False, indent=2)
            push_id = insert_cloud_push(
                conn,
                campaign_id=campaign_id,
                cloud_base_url=cloud_base_url,
                request_hash=request_hash,
                response_json=response_json,
            )
            n = insert_cloud_push_tokens(conn, push_id=push_id, campaign_id=campaign_id, cloud_base_url=cloud_base_url, tokens=tokens)
            conn.commit()

        flash(f"Pushed to Cloudflare: created a new wave and stored {n} tokens.", "success")
        return redirect(url_for("master_view", campaign_key=campaign_key))

    @app.get("/campaigns/<campaign_key>/cloud/tokens.csv")
    def cloud_tokens_csv(campaign_key: str) -> Response:
        cloud_base_url, _ = get_cloud_config()
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                return Response("Campaign not found", status=404)
            if not cloud_base_url:
                return Response("CLOUDFLARE_STUDY_BASE_URL not set", status=400)
            rows = list_cloud_latest_tokens(conn, campaign_id=int(campaign["id"]), cloud_base_url=cloud_base_url)

        lines = ["email,token,link"]
        for r in rows:
            token = str(r["cloud_token"])
            link = f"{cloud_base_url}/s/{token}"
            email = str(r["email"])
            lines.append(",".join([csv_escape(email), csv_escape(token), csv_escape(link)]))
        body = "\n".join(lines) + "\n"
        return Response(
            body,
            status=200,
            mimetype="text/csv",
            headers={"Content-Disposition": f'attachment; filename=\"{campaign_key}.cloud_tokens.csv\"'},
        )

    @app.get("/campaigns/<campaign_key>/cloud/tokens_history.csv")
    def cloud_tokens_history_csv(campaign_key: str) -> Response:
        cloud_base_url, _ = get_cloud_config()
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                return Response("Campaign not found", status=404)
            if not cloud_base_url:
                return Response("CLOUDFLARE_STUDY_BASE_URL not set", status=400)
            pushes = list_cloud_pushes(conn, campaign_id=int(campaign["id"]), cloud_base_url=cloud_base_url)
            rows: list[tuple[str, str, str, str, str]] = []
            for p in pushes:
                toks = list_cloud_tokens_for_push(conn, push_id=int(p["push_id"]))
                for t in toks:
                    rows.append(
                        (
                            str(p["created_at"]),
                            str(p["request_hash"]),
                            str(t["email"]),
                            str(t["cloud_token"]),
                            f"{cloud_base_url}/s/{t['cloud_token']}",
                        )
                    )

        lines = ["push_created_at,request_hash,email,token,link"]
        for (created_at, request_hash, email, token, link) in rows:
            lines.append(",".join([csv_escape(created_at), csv_escape(request_hash), csv_escape(email), csv_escape(token), csv_escape(link)]))
        body = "\n".join(lines) + "\n"
        return Response(
            body,
            status=200,
            mimetype="text/csv",
            headers={"Content-Disposition": f'attachment; filename=\"{campaign_key}.cloud_tokens.history.csv\"'},
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
            email_yaml = email_config_to_yaml(
                from_email=email_from,
                subject=email_subject,
                base_url=email_base_url or "http://127.0.0.1:5055",
                html=email_html,
            )
            update_campaign_email_yaml(conn, campaign_key=campaign_key, email_yaml=email_yaml)
            log_event(conn, campaign=campaign, event="update_email_settings", success=True)
            conn.commit()

        flash("Saved email settings (synced to YAML)", "success")
        return redirect(url_for("master_view", campaign_key=campaign_key))

    @app.post("/campaigns/<campaign_key>/send-emails")
    def send_emails(campaign_key: str) -> Response:
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                flash("Campaign not found", "error")
                return redirect(url_for("home"))

            inv_rows = list_invitations_for_campaign(conn, campaign_id=int(campaign["id"]))
            if not inv_rows:
                flash("No invitations yet. Click Generate/Prepare first.", "error")
                return redirect(url_for("master_view", campaign_key=campaign_key))

            excluded_emails = set(str(r["email"]) for r in list_excluded_recipients_for_campaign(conn, campaign_id=int(campaign["id"])))
            inv_rows = [r for r in inv_rows if str(r["email"]) not in excluded_emails]
            if not inv_rows:
                flash("All recipients have been excluded from this campaign.", "error")
                return redirect(url_for("master_view", campaign_key=campaign_key))

            if campaign["picker_strategy"] != "online_assign":
                missing = [r for r in inv_rows if not r["questionnaire_json"]]
                if missing:
                    flash("Some invitations are missing questionnaire snapshots. Click Generate first.", "error")
                    return redirect(url_for("master_view", campaign_key=campaign_key))

            email_from = (campaign["email_from"] or "").strip()
            email_subject = (campaign["email_subject"] or "").strip()
            email_html = (campaign["email_html"] or "").strip()
            base_url = (campaign["email_base_url"] or "http://127.0.0.1:5055").strip()
            base_url_norm = base_url.rstrip("/")
            cloud_base_url, _ = get_cloud_config()
            is_local_base = base_url_norm.startswith("http://127.0.0.1") or base_url_norm.startswith("http://localhost")
            mode = admin_mode_from_conn(conn)

            if not email_from or not email_subject or not email_html:
                log_event(conn, campaign=campaign, event="send_emails", success=False, message="Missing email settings")
                flash("Missing email settings: email_from, email_subject, and email_html are required.", "error")
                return redirect(url_for("master_view", campaign_key=campaign_key))

            if mode == "local" and not is_local_base:
                log_event(conn, campaign=campaign, event="send_emails", success=False, message="Local mode requires local email_base_url")
                flash(
                    "In Local mode, set email_base_url to a local URL (e.g. http://127.0.0.1:5055). Switch to Cloud mode if you want to email Cloudflare links.",
                    "error",
                )
                return redirect(url_for("master_view", campaign_key=campaign_key))

            if mode == "cloud":
                if is_local_base:
                    log_event(conn, campaign=campaign, event="send_emails", success=False, message="Cloud mode requires non-local email_base_url")
                    flash(
                        "In Cloud mode, set email_base_url to your Cloudflare study site (e.g. https://study-staging.hvp.global).",
                        "error",
                    )
                    return redirect(url_for("master_view", campaign_key=campaign_key))

                if cloud_base_url and base_url_norm != cloud_base_url:
                    flash(
                        "Warning: email_base_url does not match CLOUDFLARE_STUDY_BASE_URL. Using email_base_url to select Cloudflare tokens.",
                        "warning",
                    )

                target_cloud_base_url = base_url_norm
                cloud_rows = list_cloud_latest_tokens(conn, campaign_id=int(campaign["id"]), cloud_base_url=target_cloud_base_url)
                if not cloud_rows:
                    known = [
                        str(r["cloud_base_url"])
                        for r in conn.execute(
                            "SELECT DISTINCT cloud_base_url FROM cloud_pushes WHERE campaign_id = ? ORDER BY cloud_base_url",
                            (int(campaign["id"]),),
                        ).fetchall()
                    ]
                    log_event(
                        conn,
                        campaign=campaign,
                        event="send_emails",
                        success=False,
                        message=f"No Cloudflare tokens found for base_url={target_cloud_base_url}",
                    )
                    hint = ""
                    if known:
                        hint = " Known Cloudflare base URLs for this campaign: " + ", ".join(known)
                    flash(
                        "No Cloudflare tokens found for this email_base_url. In Master view, run “Push to Cloudflare (generate tokens)” first." + hint,
                        "error",
                    )
                    return redirect(url_for("master_view", campaign_key=campaign_key))

                cloud_token_by_email = {str(r["email"]): str(r["cloud_token"]) for r in cloud_rows}
                missing = [str(r["email"]) for r in inv_rows if str(r["email"]) not in cloud_token_by_email]
                if missing:
                    log_event(
                        conn,
                        campaign=campaign,
                        event="send_emails",
                        success=False,
                        message=f"Missing Cloudflare tokens for {len(missing)} recipients",
                    )
                    sample = ", ".join(missing[:5]) + ("…" if len(missing) > 5 else "")
                    flash(
                        f"Missing Cloudflare tokens for {len(missing)} recipients. Push to Cloudflare again. Sample: {sample}",
                        "error",
                    )
                    return redirect(url_for("master_view", campaign_key=campaign_key))

                inv_rows = [dict(r) | {"token": cloud_token_by_email[str(r["email"])]} for r in inv_rows]

            try:
                template_id = create_or_update_campaign_template(
                    campaign_key=campaign_key,
                    template_id=(campaign["email_template_id"] or None),
                    from_email=email_from,
                    subject=email_subject,
                    html=email_html,
                )
            except ResendError as e:
                log_event(conn, campaign=campaign, event="send_emails", success=False, message=f"Template update error: {e}")
                flash(f"Resend error creating/updating template: {e}", "error")
                return redirect(url_for("master_view", campaign_key=campaign_key))

            conn.execute(
                "UPDATE campaigns SET email_template_id = ? WHERE campaign_key = ?",
                (template_id, campaign_key),
            )
            conn.commit()

            name_map = recipient_name_map(conn, emails=[str(r["email"]) for r in inv_rows])

        def _log_send_result(*, success: bool, message: str | None) -> None:
            with db.connect() as log_conn:
                fresh_campaign = get_campaign_by_key(log_conn, campaign_key=campaign_key)
                if fresh_campaign is None:
                    return
                log_event(log_conn, campaign=fresh_campaign, event="send_emails", success=success, message=message)
                log_conn.commit()

        try:
            sends = send_invites_for_campaign(
                template_id=template_id,
                campaign_title=str(campaign["title"]),
                base_url=base_url,
                invitations=[
                    {
                        "email": r["email"],
                        "token": r["token"],
                        "first_name": (name_map.get(str(r["email"])) or {}).get("firstname", ""),
                        "last_name": (name_map.get(str(r["email"])) or {}).get("lastname", ""),
                    }
                    for r in inv_rows
                ],
            )
        except ResendError as e:
            _log_send_result(success=False, message=f"Resend send error: {e}")
            flash(f"Resend send error: {e}", "error")
            return redirect(url_for("master_view", campaign_key=campaign_key))

        flash(f"Sent {len(sends)} invitation emails.", "success")
        _log_send_result(success=True, message=f"Sent {len(sends)} messages")
        return redirect(url_for("master_view", campaign_key=campaign_key))

    @app.get("/campaigns/<campaign_key>/email-preview")
    def email_preview(campaign_key: str) -> str:
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                flash("Campaign not found", "error")
                return redirect(url_for("home"))
            inv_rows = list_invitations_for_campaign(conn, campaign_id=int(campaign["id"]))
            if not inv_rows:
                flash("No invitations yet. Click Generate/Prepare first.", "error")
                return redirect(url_for("master_view", campaign_key=campaign_key))

            excluded_emails = set(str(r["email"]) for r in list_excluded_recipients_for_campaign(conn, campaign_id=int(campaign["id"])))
            inv_rows = [r for r in inv_rows if str(r["email"]) not in excluded_emails]
            if not inv_rows:
                flash("All recipients have been excluded from this campaign.", "error")
                return redirect(url_for("master_view", campaign_key=campaign_key))

            email_from = (campaign["email_from"] or "").strip()
            email_subject = (campaign["email_subject"] or "").strip()
            email_html = (campaign["email_html"] or "").strip()
            base_url = (campaign["email_base_url"] or "http://127.0.0.1:5055").strip()
            base_url_norm = base_url.rstrip("/")
            cloud_base_url, _ = get_cloud_config()
            is_local_base = base_url_norm.startswith("http://127.0.0.1") or base_url_norm.startswith("http://localhost")
            mode = admin_mode_from_conn(conn)

            if not email_from or not email_subject or not email_html:
                flash("Missing email settings: email_from, email_subject, and email_html are required.", "error")
                return redirect(url_for("master_view", campaign_key=campaign_key))

            if mode == "local" and not is_local_base:
                flash(
                    "In Local mode, email_base_url should be local (e.g. http://127.0.0.1:5055). Preview links may be invalid otherwise.",
                    "warning",
                )

            if mode == "cloud":
                if is_local_base:
                    flash(
                        "In Cloud mode, set email_base_url to your Cloudflare study site (e.g. https://study-staging.hvp.global).",
                        "error",
                    )
                    return redirect(url_for("master_view", campaign_key=campaign_key))

                if cloud_base_url and base_url_norm != cloud_base_url:
                    flash(
                        "Warning: email_base_url does not match CLOUDFLARE_STUDY_BASE_URL. Using email_base_url to select Cloudflare tokens.",
                        "warning",
                    )
                cloud_rows = list_cloud_latest_tokens(conn, campaign_id=int(campaign["id"]), cloud_base_url=base_url_norm)
                cloud_token_by_email = {str(r["email"]): str(r["cloud_token"]) for r in cloud_rows}
                inv_rows = [dict(r) | {"token": cloud_token_by_email.get(str(r["email"]))} for r in inv_rows]

            name_map = recipient_name_map(conn, emails=[str(r["email"]) for r in inv_rows])

            previews: list[dict[str, str]] = []
            for r in inv_rows:
                if isinstance(r, dict):
                    intended_email = str(r.get("email") or "")
                    token = str(r.get("token") or "")
                else:
                    intended_email = str(r["email"])
                    token = str(r["token"] or "")
                if not token:
                    token = "MISSING_TOKEN"
                link = base_url.rstrip("/") + f"/s/{token}"
                nm = name_map.get(intended_email) or {}
                variables = {
                    "SURVEY_LINK": link,
                    "CAMPAIGN_TITLE": str(campaign["title"]),
                    "RECIPIENT_EMAIL": intended_email,
                    "RECIPIENT_FIRST_NAME": nm.get("firstname", ""),
                    "RECIPIENT_LAST_NAME": nm.get("lastname", ""),
                }
                rendered_html = render_email_preview(html=email_html, variables=variables)
                rendered_text = _html_to_plain_text(rendered_html)
                previews.append(
                    {
                        "intended_email": intended_email,
                        "token": token,
                        "survey_link": link,
                        "rendered_html": rendered_html,
                        "rendered_text": rendered_text,
                    }
                )

        return render_template(
            "email_preview.html",
            campaign=campaign,
            admin_mode=mode,
            email_from=email_from,
            email_subject=email_subject,
            previews=previews,
        )

    @app.get("/campaigns/<campaign_key>/recipients")
    def campaign_recipients(campaign_key: str) -> str:
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                flash("Campaign not found", "error")
                return redirect(url_for("home"))

            pending = list_pending_recipients_for_campaign(conn, campaign_id=int(campaign["id"]))
            excluded = list_excluded_recipients_for_campaign(conn, campaign_id=int(campaign["id"]))
            counts = count_pending_recipients(conn, campaign_id=int(campaign["id"]))

            def parse_recipient(row: sqlite3.Row) -> dict[str, Any]:
                strata = parse_json_obj(row["strata_json"] if "strata_json" in row else None)
                return {
                    "email": row["email"],
                    "firstname": strata.get("firstname", ""),
                    "lastname": strata.get("lastname", ""),
                    "token": row["token"] if "token" in row else None,
                    "opened_at": row["opened_at"] if "opened_at" in row else None,
                    "questionnaire_hash": row["questionnaire_hash"] if "questionnaire_hash" in row else None,
                    "has_submitted": bool(row["has_submitted"]) if "has_submitted" in row else False,
                }

            def parse_excluded(row: sqlite3.Row) -> dict[str, Any]:
                strata = parse_json_obj(row["strata_json"] if "strata_json" in row else None)
                return {
                    "email": row["email"],
                    "firstname": strata.get("firstname", ""),
                    "lastname": strata.get("lastname", ""),
                    "excluded_at": row["excluded_at"],
                }

            pending_parsed = [parse_recipient(r) for r in pending]
            excluded_parsed = [parse_excluded(r) for r in excluded]

            not_sent = [r for r in pending_parsed if not r["has_submitted"]]
            submitted = [r for r in pending_parsed if r["has_submitted"]]

        return render_template(
            "recipients.html",
            campaign=campaign,
            not_sent=not_sent,
            submitted=submitted,
            excluded=excluded_parsed,
            counts=counts,
        )

    @app.post("/campaigns/<campaign_key>/recipients/exclude")
    def exclude_recipient(campaign_key: str) -> Response:
        email = (request.form.get("email") or "").strip().lower()
        if not email:
            flash("Email is required", "error")
            return redirect(url_for("campaign_recipients", campaign_key=campaign_key))

        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                flash("Campaign not found", "error")
                return redirect(url_for("home"))

            added = exclude_recipient_from_campaign(conn, campaign_id=int(campaign["id"]), email=email)
            if added:
                log_event(conn, campaign=campaign, event="exclude_recipient", success=True, message=f"Excluded {email}")
                flash(f"Excluded {email} from this campaign", "success")
            else:
                flash(f"{email} was already excluded", "warning")
            conn.commit()

        return redirect(url_for("campaign_recipients", campaign_key=campaign_key))

    @app.post("/campaigns/<campaign_key>/recipients/restore")
    def restore_recipient(campaign_key: str) -> Response:
        email = (request.form.get("email") or "").strip().lower()
        if not email:
            flash("Email is required", "error")
            return redirect(url_for("campaign_recipients", campaign_key=campaign_key))

        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                flash("Campaign not found", "error")
                return redirect(url_for("home"))

            removed = restore_recipient_to_campaign(conn, campaign_id=int(campaign["id"]), email=email)
            if removed:
                log_event(conn, campaign=campaign, event="restore_recipient", success=True, message=f"Restored {email}")
                flash(f"Restored {email} to this campaign", "success")
            else:
                flash(f"{email} was not excluded", "warning")
            conn.commit()

        return redirect(url_for("campaign_recipients", campaign_key=campaign_key))

    # -----------------------------
    # Exports + previews + stats
    # -----------------------------
    @app.get("/campaigns/<campaign_key>/export_invitations.json")
    def export_invitations_json(campaign_key: str) -> Response:
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
        return render_template(
            "preview.html",
            campaign=campaign,
            email=row["email"],
            qjson=qjson,
            layout_config=normalize_layout_config(str(campaign["layout_yaml"] or DEFAULT_LAYOUT_YAML)),
        )

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

    # -----------------------------
    # Railway API endpoints
    # -----------------------------
    def _require_railway_auth() -> str | None:
        """Check Railway admin token authentication. Returns error message or None."""
        _, railway_admin_token = get_railway_config()
        if not railway_admin_token:
            return "RAILWAY_ADMIN_TOKEN not configured on server"
        
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return "Missing Authorization header"
        
        provided_token = auth_header[7:].strip()
        if provided_token != railway_admin_token:
            return "Invalid admin token"
        
        return None

    @app.post("/api/railway/sync_question_bank")
    def railway_sync_question_bank() -> Response:
        """
        API endpoint for uploading question items and stats to Railway PostgreSQL.
        Used by local admin UI to push online_assign campaigns.
        """
        auth_error = _require_railway_auth()
        if auth_error:
            return Response(json.dumps({"error": auth_error}), status=401, mimetype="application/json")
        
        try:
            payload = request.get_json()
            if not payload:
                return Response(json.dumps({"error": "No JSON payload"}), status=400, mimetype="application/json")
            
            campaign_key = str(payload.get("campaignKey", "")).strip()
            question_items = payload.get("questionItems", [])
            
            if not campaign_key:
                return Response(json.dumps({"error": "campaignKey required"}), status=400, mimetype="application/json")
            if not isinstance(question_items, list):
                return Response(json.dumps({"error": "questionItems must be array"}), status=400, mimetype="application/json")
            
            with db.connect() as conn:
                # Get or create campaign
                campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
                if not campaign:
                    return Response(json.dumps({"error": f"Campaign '{campaign_key}' not found"}), status=404, mimetype="application/json")
                
                campaign_id = int(campaign["id"])
                
                # Upsert question items
                items_upserted = 0
                for item in question_items:
                    item_id = str(item.get("itemId", "")).strip()
                    if not item_id:
                        continue
                    
                    conn.execute(
                        """
                        INSERT INTO question_items (campaign_id, item_id, source_kind, source_id, vignette, prompt, choices_json, tags_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(campaign_id, item_id) DO UPDATE SET
                            source_kind = excluded.source_kind,
                            source_id = excluded.source_id,
                            vignette = excluded.vignette,
                            prompt = excluded.prompt,
                            choices_json = excluded.choices_json,
                            tags_json = excluded.tags_json
                        """ if not db.is_postgres else """
                        INSERT INTO question_items (campaign_id, item_id, source_kind, source_id, vignette, prompt, choices_json, tags_json)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT(campaign_id, item_id) DO UPDATE SET
                            source_kind = EXCLUDED.source_kind,
                            source_id = EXCLUDED.source_id,
                            vignette = EXCLUDED.vignette,
                            prompt = EXCLUDED.prompt,
                            choices_json = EXCLUDED.choices_json,
                            tags_json = EXCLUDED.tags_json
                        """,
                        (
                            campaign_id,
                            item_id,
                            item.get("sourceKind", "case"),
                            item.get("sourceId", item_id),
                            item.get("vignette", ""),
                            item.get("prompt", ""),
                            item.get("choicesJson", "[]"),
                            item.get("tagsJson", "{}"),
                        ),
                    )
                    
                    # Also initialize question_stats
                    conn.execute(
                        """
                        INSERT INTO question_stats (campaign_id, item_id, assigned_count, submitted_count)
                        VALUES (?, ?, 0, 0)
                        ON CONFLICT(campaign_id, item_id) DO NOTHING
                        """ if not db.is_postgres else """
                        INSERT INTO question_stats (campaign_id, item_id, assigned_count, submitted_count)
                        VALUES (%s, %s, 0, 0)
                        ON CONFLICT(campaign_id, item_id) DO NOTHING
                        """,
                        (campaign_id, item_id),
                    )
                    items_upserted += 1
                
                conn.commit()
            
            return Response(
                json.dumps({"success": True, "itemsUpserted": items_upserted}),
                status=200,
                mimetype="application/json",
            )
        
        except Exception as e:
            return Response(
                json.dumps({"error": str(e)}),
                status=500,
                mimetype="application/json",
            )

    @app.post("/api/railway/sync_campaign")
    def railway_sync_campaign() -> Response:
        """
        API endpoint for creating campaign and invitations on Railway PostgreSQL.
        Used by local admin UI to push online_assign campaigns.
        """
        auth_error = _require_railway_auth()
        if auth_error:
            return Response(json.dumps({"error": auth_error}), status=401, mimetype="application/json")
        
        try:
            payload = request.get_json()
            if not payload:
                return Response(json.dumps({"error": "No JSON payload"}), status=400, mimetype="application/json")
            
            campaign_key = str(payload.get("campaignKey", "")).strip()
            title = str(payload.get("title", "")).strip()
            seed = int(payload.get("seed", 0))
            questionnaire_version = int(payload.get("questionnaireVersion", 1))
            picker_strategy = str(payload.get("pickerStrategy", "online_assign")).strip()
            k = int(payload.get("k", 1))
            layout_yaml = payload.get("layoutYaml", "")
            recipients = payload.get("recipients", [])
            
            if not campaign_key or not title:
                return Response(json.dumps({"error": "campaignKey and title required"}), status=400, mimetype="application/json")
            if not isinstance(recipients, list):
                return Response(json.dumps({"error": "recipients must be array"}), status=400, mimetype="application/json")
            
            with db.connect() as conn:
                # Upsert campaign
                campaign_id = upsert_campaign(
                    conn,
                    campaign_key=campaign_key,
                    title=title,
                    seed=seed,
                    questionnaire_version=questionnaire_version,
                )
                
                # Update campaign with picker_strategy and k
                conn.execute(
                    """
                    UPDATE campaigns
                    SET picker_strategy = ?, k = ?, layout_yaml = ?
                    WHERE id = ?
                    """ if not db.is_postgres else """
                    UPDATE campaigns
                    SET picker_strategy = %s, k = %s, layout_yaml = %s
                    WHERE id = %s
                    """,
                    (picker_strategy, k, layout_yaml, campaign_id),
                )
                
                # Create invitations for recipients
                tokens: list[dict[str, str]] = []
                for recipient in recipients:
                    email = str(recipient.get("email", "")).strip().lower()
                    if not email:
                        continue
                    
                    # Generate token
                    token = secrets.token_urlsafe(24)
                    
                    # Insert invitation (questionnaire_json will be filled on first access)
                    conn.execute(
                        """
                        INSERT INTO invitations (campaign_id, email, token)
                        VALUES (?, ?, ?)
                        ON CONFLICT(campaign_id, email) DO UPDATE SET
                            token = excluded.token
                        """ if not db.is_postgres else """
                        INSERT INTO invitations (campaign_id, email, token)
                        VALUES (%s, %s, %s)
                        ON CONFLICT(campaign_id, email) DO UPDATE SET
                            token = EXCLUDED.token
                        """,
                        (campaign_id, email, token),
                    )
                    
                    tokens.append({"email": email, "token": token})
                
                conn.commit()
            
            return Response(
                json.dumps({
                    "success": True,
                    "campaignId": campaign_id,
                    "invitationsCreated": len(tokens),
                    "tokens": tokens,
                }),
                status=200,
                mimetype="application/json",
            )
        
        except Exception as e:
            return Response(
                json.dumps({"error": str(e)}),
                status=500,
                mimetype="application/json",
            )

    @app.post("/campaigns/<campaign_key>/railway/push")
    def railway_push(campaign_key: str) -> Response:
        """
        UI route to push an online_assign campaign to Railway from local admin.
        """
        railway_base_url, railway_admin_token = get_railway_config()
        if not railway_base_url or not railway_admin_token:
            flash("Missing env vars: RAILWAY_APP_URL and RAILWAY_ADMIN_TOKEN are required.", "error")
            return redirect(url_for("master_view", campaign_key=campaign_key))
        
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                flash("Campaign not found", "error")
                return redirect(url_for("home"))
            
            if campaign["picker_strategy"] != "online_assign":
                flash("Railway push is only for online_assign campaigns. Use Cloud push for pick_k_cases/template_expand.", "error")
                return redirect(url_for("master_view", campaign_key=campaign_key))
            
            campaign_id = int(campaign["id"])
            
            # Step 1: Extract question items
            question_items = list_question_items_with_stats(conn, campaign_id=campaign_id)
            if not question_items:
                flash("No question bank yet. Click 'Generate question bank' first.", "error")
                return redirect(url_for("master_view", campaign_key=campaign_key))
            
            items_payload: list[dict[str, Any]] = []
            for item in question_items:
                items_payload.append({
                    "itemId": item["item_id"],
                    "sourceKind": item["source_kind"],
                    "sourceId": item["source_id"],
                    "vignette": item["vignette"],
                    "prompt": item["prompt"],
                    "choicesJson": item["choices_json"],
                    "tagsJson": item["tags_json"],
                })
            
            # Step 2: Extract recipients
            recipients_list = list_pending_recipients_for_campaign(conn, campaign_id=campaign_id)
            recipients_payload: list[dict[str, str]] = []
            for r in recipients_list:
                recipients_payload.append({"email": r["email"]})
            
            if not recipients_payload:
                flash("No recipients to push. Import recipients first.", "error")
                return redirect(url_for("master_view", campaign_key=campaign_key))
            
            # Step 3: Push campaign metadata FIRST (creates campaign on Railway)
            # This must happen before pushing question bank, since question bank requires campaign to exist
            try:
                # Extract campaign fields with safe fallbacks (sqlite3.Row doesn't have .get())
                try:
                    k_val = int(campaign["k"]) if campaign["k"] is not None else 1
                except (KeyError, TypeError, ValueError):
                    k_val = 1
                
                try:
                    layout_yaml_val = campaign["layout_yaml"] or ""
                except (KeyError, TypeError):
                    layout_yaml_val = ""
                
                resp_campaign = cloud_post_json(
                    url=f"{railway_base_url}/api/railway/sync_campaign",
                    bearer_token=railway_admin_token,
                    payload_obj={
                        "campaignKey": campaign_key,
                        "title": campaign["title"],
                        "seed": int(campaign["seed"]),
                        "questionnaireVersion": int(campaign["questionnaire_version"]),
                        "pickerStrategy": campaign["picker_strategy"],
                        "k": k_val,
                        "layoutYaml": layout_yaml_val,
                        "recipients": recipients_payload,
                    },
                )
                if not resp_campaign.get("success"):
                    flash(f"Railway campaign push failed: {resp_campaign.get('error', 'Unknown error')}", "error")
                    return redirect(url_for("master_view", campaign_key=campaign_key))
            except Exception as e:
                flash(f"Railway campaign push failed: {e}", "error")
                log_event(conn, campaign=campaign, event="railway_push", success=False, message=str(e))
                return redirect(url_for("master_view", campaign_key=campaign_key))
            
            # Step 4: Now push question bank (campaign exists on Railway now)
            try:
                resp_questions = cloud_post_json(
                    url=f"{railway_base_url}/api/railway/sync_question_bank",
                    bearer_token=railway_admin_token,
                    payload_obj={"campaignKey": campaign_key, "questionItems": items_payload},
                )
                if not resp_questions.get("success"):
                    flash(f"Railway question bank push failed: {resp_questions.get('error', 'Unknown error')}", "error")
                    return redirect(url_for("master_view", campaign_key=campaign_key))
            except Exception as e:
                flash(f"Railway question bank push failed: {e}", "error")
                return redirect(url_for("master_view", campaign_key=campaign_key))
            
            # Save tokens to cloud_invitation_tokens table for tracking
            tokens = resp_campaign.get("tokens", [])
            if tokens:
                insert_cloud_push(conn, campaign_id=campaign_id, cloud_base_url=railway_base_url, request_hash="railway_push")
                push_id = conn.execute("SELECT last_insert_rowid()" if not db.is_postgres else "SELECT currval(pg_get_serial_sequence('cloud_pushes', 'id'))").fetchone()[0]
                insert_cloud_push_tokens(conn, push_id=int(push_id), tokens_map={t["email"]: t["token"] for t in tokens})
                conn.commit()
            
            flash(
                f"Successfully pushed to Railway! Campaign + {resp_questions.get('itemsUpserted', 0)} questions + {len(tokens)} invitations created. "
                f"Survey links: {railway_base_url}/s/<token>",
                "success",
            )
            log_event(conn, campaign=campaign, event="railway_push", success=True)
        
        return redirect(url_for("master_view", campaign_key=campaign_key))


