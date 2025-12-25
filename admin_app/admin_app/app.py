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
    increment_assigned_count,
    insert_variants,
    list_campaigns,
    list_assignments_for_token,
    list_invitations_for_campaign,
    list_question_items_with_stats,
    load_cases,
    load_recipients,
    load_templates,
    mark_invitation_opened,
    save_invitation_snapshot,
    insert_assignment,
    upsert_campaign,
    upsert_cases,
    upsert_recipients,
    upsert_templates,
    upsert_question_items_from_cases,
    variant_counts,
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
        Simulates the respondent link-open flow (assignment happens on first GET).
        """
        with db.connect() as conn:
            inv = get_invitation_by_token(conn, token=token)
            if inv is None:
                return Response("Invalid token", status=404)
            campaign = conn.execute("SELECT * FROM campaigns WHERE id = ?", (int(inv["campaign_id"]),)).fetchone()
            if campaign is None:
                return Response("Campaign not found", status=404)
            if campaign["picker_strategy"] != "online_assign":
                return Response("This invitation is not for an online_assign campaign", status=400)
            mark_invitation_opened(conn, token=token)
            qjson = _assign_on_open(conn=conn, campaign_row=campaign, invitation_row=inv)
            conn.commit()
        return Response(
            render_template("respondent.html", campaign=campaign, email=inv["email"], token=token, qjson=qjson),
            status=200,
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


