from __future__ import annotations

import hashlib
import json
import os
import ssl
import sqlite3
import urllib.error
import urllib.request
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
    list_invitation_ledger_rows,
    list_recent_submissions,
    list_question_items_with_stats,
    list_free_text_answers,
    load_cases,
    load_recipients,
    load_templates,
    mark_invitation_opened,
    populate_invitations_from_variants,
    report_rows,
    save_invitation_snapshot,
    single_select_choice_counts,
    insert_assignment,
    get_last_cloud_push,
    insert_cloud_push,
    insert_cloud_push_tokens,
    list_cloud_latest_tokens,
    list_cloud_pushes,
    list_cloud_tokens_for_push,
    submission_cohort_counts,
    upsert_campaign,
    upsert_cases,
    upsert_recipients,
    upsert_templates,
    upsert_question_items_from_cases,
    variant_counts,
    record_event,
)

from .resend_client import ResendError, create_or_update_campaign_template, send_invites_for_campaign

def _render_email_preview(*, html: str, variables: dict[str, str]) -> str:
    # Very small, safe placeholder replacement for triple-brace variables.
    # We do not execute any HTML/JS; the template itself is HTML, we just substitute strings.
    out = html
    for k, v in variables.items():
        out = out.replace(f"{{{{{{{k}}}}}}}", v)
    return out


def _recipient_name_map(conn, *, emails: list[str]) -> dict[str, dict[str, str]]:
    """
    Returns mapping email -> {firstname, lastname} from recipients.strata_json.
    """
    if not emails:
        return {}
    rows = conn.execute("SELECT email, strata_json FROM recipients WHERE email IN (%s)" % (",".join(["?"] * len(emails))), emails).fetchall()
    out: dict[str, dict[str, str]] = {}
    for r in rows:
        try:
            strata = json.loads(r["strata_json"]) if r["strata_json"] else {}
        except Exception:
            strata = {}
        out[str(r["email"])] = {
            "firstname": str(strata.get("firstname") or ""),
            "lastname": str(strata.get("lastname") or ""),
        }
    return out


def _canonical_json_bytes(obj: Any) -> bytes:
    # Deterministic JSON encoding for hashing requests
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _cloud_post_json(*, url: str, bearer_token: str, payload_obj: Any, timeout_sec: int = 30) -> dict[str, Any]:
    data = _canonical_json_bytes(payload_obj)
    req = urllib.request.Request(
        url=url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        # macOS/Homebrew Python often lacks a system CA bundle; prefer certifi if available.
        cafile: str | None = None
        try:
            import certifi  # type: ignore

            cafile = certifi.where()
        except Exception:
            cafile = None

        ctx = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()

        with urllib.request.urlopen(req, timeout=timeout_sec, context=ctx) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(body)
            except Exception as e:
                raise RuntimeError(f"Cloud response is not JSON (status {resp.status}): {body[:300]}") from e
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        raise RuntimeError(f"Cloud HTTP {e.code}: {body[:600]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cloud connection error: {e}") from e


def _log_event(
    conn: sqlite3.Connection,
    *,
    campaign: sqlite3.Row | None,
    event: str,
    success: bool,
    message: str | None = None,
) -> None:
    from .db import record_event

    campaign_id = int(campaign["id"]) if campaign is not None else None
    campaign_key = str(campaign["campaign_key"]) if campaign is not None else None
    record_event(
        conn,
        campaign_id=campaign_id,
        campaign_key=campaign_key,
        event=event,
        success=success,
        message=message,
    )


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
            with db.connect() as conn:
                _log_event(conn, campaign=None, event="import_cases", success=False, message=str(e))
            flash(f"Failed to parse cases.csv: {e}", "error")
            return redirect(request.referrer or url_for("home"))

        with db.connect() as conn:
            n = upsert_cases(conn, cases)
            conn.commit()
            _log_event(conn, campaign=None, event="import_cases", success=True, message=f"Imported {n} cases")
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
            with db.connect() as conn:
                _log_event(conn, campaign=None, event="import_recipients", success=False, message=str(e))
            flash(f"Failed to parse recipients.csv: {e}", "error")
            return redirect(request.referrer or url_for("home"))

        with db.connect() as conn:
            n = upsert_recipients(conn, recs)
            conn.commit()
            _log_event(conn, campaign=None, event="import_recipients", success=True, message=f"Imported {n} recipients")
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
            with db.connect() as conn:
                _log_event(conn, campaign=None, event="import_templates", success=False, message=str(e))
            flash(f"Failed to parse templates.csv: {e}", "error")
            return redirect(request.referrer or url_for("home"))

        with db.connect() as conn:
            n = upsert_templates(conn, templates)
            conn.commit()
            _log_event(conn, campaign=None, event="import_templates", success=True, message=f"Imported {n} templates")
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
                _log_event(conn, campaign=campaign, event="generate_variants", success=False, message="No recipients imported yet")
                return redirect(url_for("campaign_detail", campaign_key=campaign_key))

            picker_strategy = (campaign["picker_strategy"] if "picker_strategy" in campaign.keys() else "pick_k_cases")  # type: ignore[attr-defined]
            k = int(campaign["k"]) if "k" in campaign.keys() else 1  # type: ignore[attr-defined]

            if picker_strategy == "online_assign":
                if not cases:
                    flash("No cases imported yet. Import cases.csv first.", "error")
                    _log_event(conn, campaign=campaign, event="generate_variants", success=False, message="No cases imported yet")
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
                    _log_event(conn, campaign=campaign, event="generate_variants", success=False, message="No cases imported yet")
                    return redirect(url_for("campaign_detail", campaign_key=campaign_key))
            elif picker_strategy == "template_expand":
                if not templates:
                    flash("No templates imported yet. Import templates.csv first.", "error")
                    _log_event(conn, campaign=campaign, event="generate_variants", success=False, message="No templates imported yet")
                    return redirect(url_for("campaign_detail", campaign_key=campaign_key))
                pv = campaign["param_vector_json"] if "param_vector_json" in campaign.keys() else None  # type: ignore[attr-defined]
                if not pv:
                    flash("No param_vector.json uploaded for this campaign.", "error")
                    _log_event(conn, campaign=campaign, event="generate_variants", success=False, message="Missing param_vector")
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
            _log_event(
                conn,
                campaign=campaign,
                event="generate_variants",
                success=True,
                message=f"Generated {len(payload['invitations'])} variants",
            )
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
                insert_submission_answer(
                    conn,
                    campaign_id=campaign_id,
                    token=token,
                    block_id=bid,
                    block_type=btype,
                    value_text=value_text,
                    value_choice_id=value_choice_id,
                )

            # Increment submitted_count for each assigned item (online_assign only)
            if campaign["picker_strategy"] == "online_assign":
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

    @app.get("/campaigns/<campaign_key>/results")
    def results(campaign_key: str) -> str:
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                flash("Campaign not found", "error")
                return redirect(url_for("home"))
            campaign_id = int(campaign["id"])
            counts = submission_cohort_counts(conn, campaign_id=campaign_id)
            ss_counts = single_select_choice_counts(conn, campaign_id=campaign_id)
            ft = list_free_text_answers(conn, campaign_id=campaign_id, limit=500)
        return render_template(
            "results.html",
            campaign=campaign,
            counts=counts,
            single_select_counts=ss_counts,
            free_text_answers=ft,
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
        cloud_base_url = (os.environ.get("CLOUDFLARE_STUDY_BASE_URL") or "").strip().rstrip("/")
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
            cohort_counts = submission_cohort_counts(conn, campaign_id=campaign_id)
            recent_submissions = list_recent_submissions(conn, campaign_id=campaign_id, limit=20)
            ledger_rows = list_invitation_ledger_rows(conn, campaign_id=campaign_id)

            cloud_last_upload = None
            cloud_latest_tokens = []
            cloud_push_history: list[dict[str, Any]] = []
            events: list[Any] = []
            if cloud_base_url:
                cloud_last_upload = get_last_cloud_push(conn, campaign_id=campaign_id, cloud_base_url=cloud_base_url)
                cloud_latest_tokens = list_cloud_latest_tokens(
                    conn, campaign_id=campaign_id, cloud_base_url=cloud_base_url
                )
                pushes = list_cloud_pushes(conn, campaign_id=campaign_id, cloud_base_url=cloud_base_url)
                for p in pushes:
                    cloud_push_history.append(
                        {
                            "push": p,
                            "tokens": list_cloud_tokens_for_push(conn, push_id=int(p["push_id"])),
                        }
                    )
                events = list_events(conn, campaign_id=campaign_id, limit=20)
            else:
                events = []

        return render_template(
            "master.html",
            campaign=campaign,
            cases_n=int(cases_n),
            recipients_n=int(recipients_n),
            templates_n=int(templates_n),
            variants_counts=variants_counts,
            inv_counts=inv_counts,
            cohort_counts=cohort_counts,
            recent_submissions=recent_submissions,
            ledger_rows=ledger_rows,
            cloud_base_url=cloud_base_url,
            cloud_last_upload=cloud_last_upload,
            cloud_latest_tokens=cloud_latest_tokens,
            cloud_push_history=cloud_push_history,
            event_log=events,
        )

    @app.post("/campaigns/<campaign_key>/cloud/push")
    def cloud_push(campaign_key: str) -> Response:
        cloud_base_url = (os.environ.get("CLOUDFLARE_STUDY_BASE_URL") or "").strip().rstrip("/")
        cloud_admin_token = (os.environ.get("CLOUDFLARE_ADMIN_TOKEN") or "").strip()
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

            # Build the same payload as /export.json but in-memory (no download needed).
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
            payload = {"campaignKey": campaign_key, "invitations": invitations}
            request_hash = hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()

        # Do network call outside DB transaction.
        try:
            resp_obj = _cloud_post_json(
                url=f"{cloud_base_url}/api/admin/upload",
                bearer_token=cloud_admin_token,
                payload_obj=payload,
            )
        except Exception as e:
            flash(f"Cloud push failed: {e}", "error")
            return redirect(url_for("master_view", campaign_key=campaign_key))

        # Persist response.
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
            n = insert_cloud_push_tokens(
                conn, push_id=push_id, campaign_id=campaign_id, cloud_base_url=cloud_base_url, tokens=tokens
            )
            conn.commit()

        flash(f"Pushed to Cloudflare: created a new wave and stored {n} tokens.", "success")
        return redirect(url_for("master_view", campaign_key=campaign_key))

    @app.get("/campaigns/<campaign_key>/cloud/tokens.csv")
    def cloud_tokens_csv(campaign_key: str) -> Response:
        cloud_base_url = (os.environ.get("CLOUDFLARE_STUDY_BASE_URL") or "").strip().rstrip("/")
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                return Response("Campaign not found", status=404)
            if not cloud_base_url:
                return Response("CLOUDFLARE_STUDY_BASE_URL not set", status=400)
            rows = list_cloud_latest_tokens(conn, campaign_id=int(campaign["id"]), cloud_base_url=cloud_base_url)

        # Minimal CSV (no external deps)
        lines = ["email,token,link"]
        for r in rows:
            token = str(r["cloud_token"])
            link = f"{cloud_base_url}/s/{token}"
            email = str(r["email"])
            # naive CSV escaping for commas/quotes
            def esc(s: str) -> str:
                if any(ch in s for ch in [",", "\"", "\n", "\r"]):
                    return "\"" + s.replace("\"", "\"\"") + "\""
                return s

            lines.append(",".join([esc(email), esc(token), esc(link)]))
        body = "\n".join(lines) + "\n"
        return Response(
            body,
            status=200,
            mimetype="text/csv",
            headers={"Content-Disposition": f'attachment; filename=\"{campaign_key}.cloud_tokens.csv\"'},
        )

    @app.get("/campaigns/<campaign_key>/cloud/tokens_history.csv")
    def cloud_tokens_history_csv(campaign_key: str) -> Response:
        cloud_base_url = (os.environ.get("CLOUDFLARE_STUDY_BASE_URL") or "").strip().rstrip("/")
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                return Response("Campaign not found", status=404)
            if not cloud_base_url:
                return Response("CLOUDFLARE_STUDY_BASE_URL not set", status=400)
            pushes = list_cloud_pushes(conn, campaign_id=int(campaign["id"]), cloud_base_url=cloud_base_url)
            # Preload tokens per push
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

        def esc(s: str) -> str:
            if any(ch in s for ch in [",", "\"", "\n", "\r"]):
                return "\"" + s.replace("\"", "\"\"") + "\""
            return s

        for (created_at, request_hash, email, token, link) in rows:
            lines.append(",".join([esc(created_at), esc(request_hash), esc(email), esc(token), esc(link)]))
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
            conn.commit()
        _log_event(conn, campaign=campaign, event="update_email_settings", success=True)

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
                _log_event(
                    conn,
                    campaign=campaign,
                    event="send_emails",
                    success=False,
                    message="Missing email settings",
                )
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
                _log_event(
                    conn,
                    campaign=campaign,
                    event="send_emails",
                    success=False,
                    message=f"Template update error: {e}",
                )
                flash(f"Resend error creating/updating template: {e}", "error")
                return redirect(url_for("master_view", campaign_key=campaign_key))

            # Persist template_id back onto campaign
            conn.execute(
                "UPDATE campaigns SET email_template_id = ? WHERE campaign_key = ?",
                (template_id, campaign_key),
            )
            conn.commit()

            name_map = _recipient_name_map(conn, emails=[str(r["email"]) for r in inv_rows])

        # Send outside transaction (still safe; recipients are forced to kohane@gmail.com)
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
            _log_event(
                conn,
                campaign=campaign,
                event="send_emails",
                success=False,
                message=f"Resend send error: {e}",
            )
            flash(f"Resend send error: {e}", "error")
            return redirect(url_for("master_view", campaign_key=campaign_key))

        flash(f"Sent {len(sends)} emails (forced delivery to kohane@gmail.com).", "success")
        _log_event(
            conn,
            campaign=campaign,
            event="send_emails",
            success=True,
            message=f"Sent {len(sends)} messages",
        )
        return redirect(url_for("master_view", campaign_key=campaign_key))

    @app.get("/campaigns/<campaign_key>/email-preview")
    def email_preview(campaign_key: str) -> str:
        """
        Dry-run preview of what would be sent (no Resend API calls).
        Shows rendered subject/from and a per-invite rendered email body with SURVEY_LINK/CAMPAIGN_TITLE/RECIPIENT_EMAIL.
        """
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                flash("Campaign not found", "error")
                return redirect(url_for("home"))
            inv_rows = list_invitations_for_campaign(conn, campaign_id=int(campaign["id"]))
            if not inv_rows:
                flash("No invitations yet. Click Generate/Prepare first.", "error")
                return redirect(url_for("master_view", campaign_key=campaign_key))

            email_from = (campaign["email_from"] or "").strip()
            email_subject = (campaign["email_subject"] or "").strip()
            email_html = (campaign["email_html"] or "").strip()
            base_url = (campaign["email_base_url"] or "http://127.0.0.1:5055").strip()

            if not email_from or not email_subject or not email_html:
                flash("Missing email settings: email_from, email_subject, and email_html are required.", "error")
                return redirect(url_for("master_view", campaign_key=campaign_key))

            name_map = _recipient_name_map(conn, emails=[str(r["email"]) for r in inv_rows])

            previews: list[dict[str, str]] = []
            for r in inv_rows:
                intended_email = str(r["email"])
                token = str(r["token"])
                link = base_url.rstrip("/") + f"/s/{token}"
                nm = name_map.get(intended_email) or {}
                variables = {
                    "SURVEY_LINK": link,
                    "CAMPAIGN_TITLE": str(campaign["title"]),
                    "RECIPIENT_EMAIL": intended_email,
                    "FIRST_NAME": nm.get("firstname", ""),
                    "LAST_NAME": nm.get("lastname", ""),
                }
                previews.append(
                    {
                        "intended_email": intended_email,
                        "forced_to": "kohane@gmail.com",
                        "token": token,
                        "survey_link": link,
                        "rendered_html": _render_email_preview(html=email_html, variables=variables),
                    }
                )

        return render_template(
            "email_preview.html",
            campaign=campaign,
            email_from=email_from,
            email_subject=email_subject,
            previews=previews,
        )

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


