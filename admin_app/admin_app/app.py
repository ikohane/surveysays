from __future__ import annotations

import hashlib
import json
import os
import ssl
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

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
    count_pending_recipients,
    create_invitations_for_campaign,
    exclude_recipient_from_campaign,
    get_campaign_by_key,
    get_cloud_last_synced_at,
    get_setting,
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
    list_excluded_recipients_for_campaign,
    list_invitations_for_campaign,
    list_invitation_ledger_rows,
    list_pending_recipients_for_campaign,
    list_recent_submissions,
    list_cloud_recent_submissions,
    list_question_items_with_stats,
    list_free_text_answers,
    list_submissions_with_answers,
    load_cases,
    load_recipients,
    load_templates,
    mark_invitation_opened,
    populate_invitations_from_variants,
    report_rows,
    restore_recipient_to_campaign,
    save_invitation_snapshot,
    single_select_choice_counts,
    insert_assignment,
    get_last_cloud_push,
    insert_cloud_push,
    insert_cloud_push_tokens,
    list_cloud_latest_tokens,
    list_cloud_pushes,
    list_cloud_tokens_for_push,
    list_cloud_invitation_ledger_rows,
    submission_cohort_counts,
    set_cloud_last_synced_at,
    upsert_campaign,
    upsert_cases,
    upsert_recipients,
    upsert_templates,
    upsert_submission_from_cloud,
    upsert_question_items_from_cases,
    variant_counts,
    record_event,
    list_events,
    set_setting,
    update_campaign_layout_yaml,
    DEFAULT_LAYOUT_YAML,
)

from .resend_client import ResendError, create_or_update_campaign_template, send_invites_for_campaign, _html_to_plain_text


def _admin_mode_from_conn(conn: sqlite3.Connection) -> str:
    """
    Global admin mode (persisted in SQLite; defaultable via env).
    Values: 'local' | 'cloud'
    """
    mode_default = (os.environ.get("ADMIN_MODE_DEFAULT") or "local").strip().lower()
    if mode_default not in ("local", "cloud"):
        mode_default = "local"
    mode = (get_setting(conn, key="admin_mode") or mode_default).strip().lower()
    if mode not in ("local", "cloud"):
        mode = mode_default
    return mode


def _parse_simple_yaml_to_obj(text: str) -> dict[str, Any]:
    """
    Minimal YAML mapping parser for our small config shape.
    - Supports nested maps via indentation (2+ spaces).
    - Supports scalars: bool, int, float, string.
    - Ignores blank lines and comments.
    If PyYAML is installed, we prefer it.
    """
    s = (text or "").strip()
    if not s:
        return {}
    try:
        import yaml  # type: ignore

        obj = yaml.safe_load(s)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        pass

    def parse_scalar(v: str) -> Any:
        v = v.strip()
        if not v:
            return ""
        low = v.lower()
        if low in ("true", "yes", "on"):
            return True
        if low in ("false", "no", "off"):
            return False
        try:
            if "." in v:
                return float(v)
            return int(v)
        except Exception:
            return v.strip('"').strip("'")

    lines = []
    for raw in s.splitlines():
        if not raw.strip():
            continue
        if raw.lstrip().startswith("#"):
            continue
        # strip inline comments (naive): only if preceded by space
        if " #" in raw:
            raw = raw.split(" #", 1)[0]
        lines.append(raw.rstrip("\n"))

    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(0, root)]

    for raw in lines:
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if ":" not in line:
            continue
        key, rest = line.split(":", 1)
        key = key.strip()
        val = rest.strip()

        # pop to correct indent level
        while stack and indent < stack[-1][0]:
            stack.pop()
        if not stack:
            stack = [(0, root)]
        cur = stack[-1][1]

        if val == "":
            child: dict[str, Any] = {}
            cur[key] = child
            stack.append((indent + 2, child))
        else:
            cur[key] = parse_scalar(val)

    return root


def _normalize_layout_config(layout_yaml: str | None) -> dict[str, Any]:
    """
    Parse YAML and normalize to a small, stable config object.
    """
    obj = _parse_simple_yaml_to_obj(layout_yaml or "")
    if not obj:
        obj = _parse_simple_yaml_to_obj(DEFAULT_LAYOUT_YAML)
    prompt_first = bool(obj.get("prompt_first", True))
    dem = obj.get("question_demarcation") if isinstance(obj.get("question_demarcation"), dict) else {}
    single = obj.get("single_select") if isinstance(obj.get("single_select"), dict) else {}
    return {
        "version": int(obj.get("version", 1) or 1),
        "promptFirst": prompt_first,
        "questionDemarcation": {
            "style": str(dem.get("style", "card") or "card"),
            "gapPx": int(dem.get("gap_px", 16) or 16),
        },
        "singleSelect": {
            "layout": str(single.get("layout", "cards") or "cards"),
            "selectedStyle": str(single.get("selected_style", "highlight") or "highlight"),
        },
    }


def _maybe_sync_cloud_submissions(
    *,
    conn: sqlite3.Connection,
    campaign: sqlite3.Row,
    cloud_base_url: str,
    cloud_admin_token: str,
    force: bool = False,
    ttl_sec: int = 60,
) -> dict[str, Any]:
    """
    Pull Cloudflare D1 submissions via /api/admin/export/<campaignKey> and upsert into local SQLite.
    Returns status dict for UI.
    """
    campaign_id = int(campaign["id"])
    campaign_key = str(campaign["campaign_key"])
    now = int(time.time())

    last_synced_s = get_cloud_last_synced_at(conn, campaign_id=campaign_id, cloud_base_url=cloud_base_url) or ""
    last_synced_epoch = 0
    try:
        last_synced_epoch = int(last_synced_s) if last_synced_s else 0
    except Exception:
        last_synced_epoch = 0

    if (not force) and last_synced_epoch and (now - last_synced_epoch) < ttl_sec:
        return {"did_sync": False, "reason": "ttl", "last_synced_epoch": last_synced_epoch}

    # Allow test harness to inject an export payload (avoids network).
    injected = (os.environ.get("SURVEYSAYS_TEST_CLOUD_EXPORT_JSON") or "").strip()
    if injected:
        export = json.loads(injected)
    else:
        export = _cloud_get_json(
            url=f"{cloud_base_url}/api/admin/export/{campaign_key}",
            bearer_token=cloud_admin_token,
            timeout_sec=20,
        )

    subs = export.get("submissions") if isinstance(export, dict) else None
    subs_list = subs if isinstance(subs, list) else []

    # token -> email mapping from local cloud token history
    tok_rows = conn.execute(
        """
        SELECT email, cloud_token
        FROM cloud_invitation_tokens
        WHERE campaign_id = ? AND cloud_base_url = ?
        """,
        (campaign_id, cloud_base_url),
    ).fetchall()
    token_to_email = {str(r["cloud_token"]): str(r["email"]) for r in tok_rows}

    n_upserted = 0
    n_answers = 0
    n_missing_email = 0
    n_missing_questionnaire = 0

    for s in subs_list:
        if not isinstance(s, dict):
            continue
        token = str(s.get("token") or "").strip()
        submitted_at = str(s.get("submitted_at") or "").strip()
        answers_json = str(s.get("answers_json") or "").strip()
        if not token or not submitted_at or not answers_json:
            continue

        email = token_to_email.get(token, "")
        if not email:
            n_missing_email += 1

        # Upsert submission row (cloud timestamp + raw answers_json string)
        upsert_submission_from_cloud(
            conn,
            campaign_id=campaign_id,
            token=token,
            email=email,
            answers_json=answers_json,
            submitted_at=submitted_at,
        )
        n_upserted += 1

        # Derive per-block answers for local analytics when possible.
        try:
            answers_obj = json.loads(answers_json)
        except Exception:
            continue
        answers_map = answers_obj.get("answers") if isinstance(answers_obj, dict) else None
        if not isinstance(answers_map, dict):
            continue

        # Find local questionnaire snapshot by email (offline campaigns store this in invitation_variants).
        qrow = None
        if email:
            qrow = conn.execute(
                """
                SELECT questionnaire_json
                FROM invitation_variants
                WHERE campaign_id = ? AND email = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (campaign_id, email),
            ).fetchone()
        if not qrow:
            n_missing_questionnaire += 1
            continue

        try:
            qjson = json.loads(str(qrow["questionnaire_json"] or "{}"))
        except Exception:
            n_missing_questionnaire += 1
            continue
        blocks = qjson.get("blocks") if isinstance(qjson, dict) else None
        if not isinstance(blocks, list):
            n_missing_questionnaire += 1
            continue

        # Replace answers for this token (idempotent re-sync).
        conn.execute("DELETE FROM submission_answers WHERE campaign_id = ? AND token = ?", (campaign_id, token))
        for b in blocks:
            if not isinstance(b, dict):
                continue
            bid = str(b.get("id") or "").strip()
            btype = str(b.get("type") or "").strip()
            if not bid or btype not in ("singleSelect", "freeText"):
                continue
            if bid not in answers_map:
                continue
            v = answers_map.get(bid)
            if btype == "singleSelect":
                if isinstance(v, str) and v.strip():
                    insert_submission_answer(
                        conn,
                        campaign_id=campaign_id,
                        token=token,
                        block_id=bid,
                        block_type=btype,
                        value_text=None,
                        value_choice_id=v.strip(),
                    )
                    n_answers += 1
            elif btype == "freeText":
                if isinstance(v, str) and v.strip():
                    insert_submission_answer(
                        conn,
                        campaign_id=campaign_id,
                        token=token,
                        block_id=bid,
                        block_type=btype,
                        value_text=v,
                        value_choice_id=None,
                    )
                    n_answers += 1

    set_cloud_last_synced_at(conn, campaign_id=campaign_id, cloud_base_url=cloud_base_url, last_synced_at=str(now))
    return {
        "did_sync": True,
        "last_synced_epoch": now,
        "cloud_submissions_seen": len(subs_list),
        "local_submissions_upserted": n_upserted,
        "local_answers_written": n_answers,
        "missing_email": n_missing_email,
        "missing_questionnaire": n_missing_questionnaire,
    }

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


def _cloud_get_json(*, url: str, bearer_token: str, timeout_sec: int = 30) -> dict[str, Any]:
    req = urllib.request.Request(
        url=url,
        method="GET",
        headers={
            "Authorization": f"Bearer {bearer_token}",
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

    tz_ny = ZoneInfo("America/New_York")

    def _parse_sql_ts(s: str) -> datetime | None:
        s = (s or "").strip()
        if not s:
            return None
        # Common formats we emit/ingest:
        # - SQLite: "YYYY-MM-DD HH:MM:SS"
        # - Cloudflare D1: "YYYY-MM-DD HH:MM:SS.sss"
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=ZoneInfo("UTC"))
            except ValueError:
                continue
        try:
            # Accept ISO-ish timestamps too.
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo("UTC"))
        except Exception:
            return None

    @app.template_filter("edt")
    def format_edt(value: Any) -> str:
        """
        Render timestamps in America/New_York.
        Assumes naive SQL timestamps are UTC (SQLite CURRENT_TIMESTAMP, D1 strftime('now')).
        """
        if value is None:
            return ""
        s = str(value).strip()
        if not s:
            return ""
        dt = _parse_sql_ts(s)
        if not dt:
            return s
        local = dt.astimezone(tz_ny)
        return local.strftime("%Y-%m-%d %H:%M:%S %Z")

    repo_root = Path(__file__).resolve().parents[2]
    db_path = Path(os.environ.get("ADMIN_APP_DB", str(repo_root / "out" / "local_admin.sqlite3")))
    db = Db(db_path)
    db.init()

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

            mode_default = (os.environ.get("ADMIN_MODE_DEFAULT") or "local").strip().lower()
            if mode_default not in ("local", "cloud"):
                mode_default = "local"
            mode = (get_setting(conn, key="admin_mode") or mode_default).strip().lower()
            if mode not in ("local", "cloud"):
                mode = mode_default
        return render_template(
            "home.html",
            campaigns=campaigns,
            db_path=str(db_path),
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
        # Checkbox: when unchecked, browsers omit the field entirely.
        replace_existing = (request.form.get("replace_existing") or "0").strip() == "1"
        try:
            text = f.stream.read().decode("utf-8")
            cases = parse_cases_csv(text)
        except Exception as e:
            with db.connect() as conn:
                _log_event(conn, campaign=None, event="import_cases", success=False, message=str(e))
            flash(f"Failed to parse cases.csv: {e}", "error")
            return redirect(request.referrer or url_for("home"))

        with db.connect() as conn:
            old_total = int(conn.execute("SELECT COUNT(*) AS n FROM cases").fetchone()["n"])
            if replace_existing:
                conn.execute("DELETE FROM cases")
            n = upsert_cases(conn, cases)
            new_total = int(conn.execute("SELECT COUNT(*) AS n FROM cases").fetchone()["n"])
            conn.commit()
            mode = "replaced" if replace_existing else "upserted"
            _log_event(
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
        return redirect(request.referrer or url_for("home"))

    @app.post("/imports/recipients")
    def import_recipients() -> Response:
        f = request.files.get("file")
        if not f:
            flash("Please choose a recipients.csv file to upload", "error")
            return redirect(request.referrer or url_for("home"))
        # Default behavior is REPLACE to avoid silently mixing recipient cohorts.
        # Checkbox: when unchecked, browsers omit the field entirely.
        replace_existing = (request.form.get("replace_existing") or "0").strip() == "1"
        try:
            text = f.stream.read().decode("utf-8")
            recs = parse_recipients_csv(text)
        except Exception as e:
            with db.connect() as conn:
                _log_event(conn, campaign=None, event="import_recipients", success=False, message=str(e))
            flash(f"Failed to parse recipients.csv: {e}", "error")
            return redirect(request.referrer or url_for("home"))

        with db.connect() as conn:
            old_total = int(conn.execute("SELECT COUNT(*) AS n FROM recipients").fetchone()["n"])
            if replace_existing:
                conn.execute("DELETE FROM recipients")
            n = upsert_recipients(conn, recs)
            new_total = int(conn.execute("SELECT COUNT(*) AS n FROM recipients").fetchone()["n"])
            conn.commit()
            mode = "replaced" if replace_existing else "upserted"
            _log_event(
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
            render_template(
                "respondent.html",
                campaign=campaign,
                email=inv["email"],
                token=token,
                qjson=qjson,
                layout_config=_normalize_layout_config(str(campaign["layout_yaml"] or DEFAULT_LAYOUT_YAML)),
            ),
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
        cloud_base_url = (os.environ.get("CLOUDFLARE_STUDY_BASE_URL") or "").strip().rstrip("/")
        cloud_admin_token = (os.environ.get("CLOUDFLARE_ADMIN_TOKEN") or "").strip()
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                flash("Campaign not found", "error")
                return redirect(url_for("home"))
            campaign_id = int(campaign["id"])
            admin_mode = _admin_mode_from_conn(conn)

            # In Cloud mode, auto-sync submissions from Cloudflare so results reflect production-like delivery.
            if admin_mode == "cloud" and cloud_base_url and cloud_admin_token:
                try:
                    sync_status = _maybe_sync_cloud_submissions(
                        conn=conn,
                        campaign=campaign,
                        cloud_base_url=cloud_base_url,
                        cloud_admin_token=cloud_admin_token,
                        force=False,
                    )
                    if sync_status.get("did_sync"):
                        conn.commit()
                except Exception:
                    # Results should still render even if cloud sync fails.
                    pass

            counts = submission_cohort_counts(conn, campaign_id=campaign_id)
            ss_counts = single_select_choice_counts(conn, campaign_id=campaign_id)
            ft = list_free_text_answers(conn, campaign_id=campaign_id, limit=500)
        return render_template(
            "results.html",
            campaign=campaign,
            admin_mode=_admin_mode_from_conn(conn),
            counts=counts,
            single_select_counts=ss_counts,
            free_text_answers=ft,
        )

    @app.get("/campaigns/<campaign_key>/submissions")
    def submissions_detail(campaign_key: str) -> str:
        """
        Show detailed per-recipient submission data with their individual answers.
        """
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                flash("Campaign not found", "error")
                return redirect(url_for("home"))
            campaign_id = int(campaign["id"])
            
            submissions_raw = list_submissions_with_answers(conn, campaign_id=campaign_id)
            
            # Parse submissions to make them template-friendly
            submissions = []
            for row in submissions_raw:
                strata = {}
                try:
                    strata = json.loads(row["strata_json"]) if row["strata_json"] else {}
                except Exception:
                    pass
                
                answers_parsed = {}
                try:
                    answers_obj = json.loads(row["answers_json"]) if row["answers_json"] else {}
                    # answers_json structure: {"answers": {"block_id": "value", ...}}
                    if isinstance(answers_obj, dict):
                        answers_parsed = answers_obj.get("answers", answers_obj)
                except Exception:
                    pass
                
                submissions.append({
                    "email": row["email"],
                    "firstname": strata.get("firstname", ""),
                    "lastname": strata.get("lastname", ""),
                    "token": row["token"],
                    "submitted_at": row["submitted_at"],
                    "answers": answers_parsed,
                })
        
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
        cloud_base_url = (os.environ.get("CLOUDFLARE_STUDY_BASE_URL") or "").strip().rstrip("/")
        cloud_admin_token = (os.environ.get("CLOUDFLARE_ADMIN_TOKEN") or "").strip()
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                flash("Campaign not found", "error")
                return redirect(url_for("home"))

            campaign_id = int(campaign["id"])
            admin_mode = _admin_mode_from_conn(conn)
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

                # In Cloud mode, auto-sync into local SQLite for analysis.
                if admin_mode == "cloud" and cloud_admin_token:
                    try:
                        cloud_sync_status = _maybe_sync_cloud_submissions(
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

            # Compute local counts AFTER possible sync.
            cohort_counts = submission_cohort_counts(conn, campaign_id=campaign_id)
            recipient_counts = count_pending_recipients(conn, campaign_id=campaign_id)
            if admin_mode == "cloud" and cloud_base_url and campaign["picker_strategy"] != "online_assign":
                recent_submissions = list_cloud_recent_submissions(
                    conn, campaign_id=campaign_id, cloud_base_url=cloud_base_url, limit=20
                )
            else:
                recent_submissions = list_recent_submissions(conn, campaign_id=campaign_id, limit=20)
            if admin_mode == "cloud" and cloud_base_url and campaign["picker_strategy"] != "online_assign":
                ledger_rows = list_cloud_invitation_ledger_rows(
                    conn, campaign_id=campaign_id, cloud_base_url=cloud_base_url
                )
            else:
                ledger_rows = list_invitation_ledger_rows(conn, campaign_id=campaign_id)

        return render_template(
            "master.html",
            campaign=campaign,
            admin_mode=admin_mode,
            layout_yaml=str(campaign["layout_yaml"] or DEFAULT_LAYOUT_YAML),
            layout_config=_normalize_layout_config(str(campaign["layout_yaml"] or DEFAULT_LAYOUT_YAML)),
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
            event_log=events,
        )

    @app.post("/campaigns/<campaign_key>/cloud/sync")
    def cloud_sync_now(campaign_key: str) -> Response:
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
            try:
                status = _maybe_sync_cloud_submissions(
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
                _normalize_layout_config(layout_yaml)  # validate/normalize
            except Exception as e:
                flash(f"Invalid layout YAML: {e}", "error")
                return redirect(url_for("master_view", campaign_key=campaign_key))
            update_campaign_layout_yaml(conn, campaign_key=campaign_key, layout_yaml=layout_yaml)
            conn.commit()
        flash("Saved layout YAML", "success")
        return redirect(url_for("master_view", campaign_key=campaign_key))

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
            payload = {
                "campaignKey": campaign_key,
                "layoutConfig": _normalize_layout_config(str(campaign["layout_yaml"] or DEFAULT_LAYOUT_YAML)),
                "invitations": invitations,
            }
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
            _log_event(conn, campaign=campaign, event="update_email_settings", success=True)
            conn.commit()

        flash("Saved email settings", "success")
        return redirect(url_for("master_view", campaign_key=campaign_key))

    @app.post("/campaigns/<campaign_key>/send-emails")
    def send_emails(campaign_key: str) -> Response:
        """
        Sends invitation emails for both online_assign and offline campaigns using a per-campaign Resend template.
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

            # Filter out excluded recipients
            excluded_emails = set(
                str(r["email"]) for r in list_excluded_recipients_for_campaign(conn, campaign_id=int(campaign["id"]))
            )
            inv_rows = [r for r in inv_rows if str(r["email"]) not in excluded_emails]
            if not inv_rows:
                flash("All recipients have been excluded from this campaign.", "error")
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
            base_url_norm = base_url.rstrip("/")
            cloud_base_url = (os.environ.get("CLOUDFLARE_STUDY_BASE_URL") or "").strip().rstrip("/")
            is_local_base = base_url_norm.startswith("http://127.0.0.1") or base_url_norm.startswith("http://localhost")
            admin_mode = _admin_mode_from_conn(conn)

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

            if admin_mode == "local" and not is_local_base:
                _log_event(
                    conn,
                    campaign=campaign,
                    event="send_emails",
                    success=False,
                    message="Local mode requires local email_base_url",
                )
                flash(
                    "In Local mode, set email_base_url to a local URL (e.g. http://127.0.0.1:5055). "
                    "Switch to Cloud mode if you want to email Cloudflare links.",
                    "error",
                )
                return redirect(url_for("master_view", campaign_key=campaign_key))

            # In Cloud mode, email Cloudflare-issued tokens (not local invitation tokens).
            if admin_mode == "cloud":
                if is_local_base:
                    _log_event(
                        conn,
                        campaign=campaign,
                        event="send_emails",
                        success=False,
                        message="Cloud mode requires non-local email_base_url",
                    )
                    flash(
                        "In Cloud mode, set email_base_url to your Cloudflare study site (e.g. https://study-staging.hvp.global).",
                        "error",
                    )
                    return redirect(url_for("master_view", campaign_key=campaign_key))

                # Use email_base_url as the source of truth (it must match the base_url used when tokens were pushed).
                if cloud_base_url and base_url_norm != cloud_base_url:
                    flash(
                        "Warning: email_base_url does not match CLOUDFLARE_STUDY_BASE_URL. Using email_base_url to select Cloudflare tokens.",
                        "warning",
                    )

                target_cloud_base_url = base_url_norm
                cloud_rows = list_cloud_latest_tokens(
                    conn, campaign_id=int(campaign["id"]), cloud_base_url=target_cloud_base_url
                )
                if not cloud_rows:
                    known = [
                        str(r["cloud_base_url"])
                        for r in conn.execute(
                            "SELECT DISTINCT cloud_base_url FROM cloud_pushes WHERE campaign_id = ? ORDER BY cloud_base_url",
                            (int(campaign["id"]),),
                        ).fetchall()
                    ]
                    _log_event(
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
                        "No Cloudflare tokens found for this email_base_url. In Master view, run “Push to Cloudflare (generate tokens)” first."
                        + hint,
                        "error",
                    )
                    return redirect(url_for("master_view", campaign_key=campaign_key))

                cloud_token_by_email = {str(r["email"]): str(r["cloud_token"]) for r in cloud_rows}
                missing = [str(r["email"]) for r in inv_rows if str(r["email"]) not in cloud_token_by_email]
                if missing:
                    _log_event(
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

                # Replace invitation tokens with Cloudflare tokens for emailing.
                inv_rows = [
                    dict(r) | {"token": cloud_token_by_email[str(r["email"])]}
                    for r in inv_rows
                ]

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

        def _log_send_result(*, success: bool, message: str | None) -> None:
            with db.connect() as log_conn:
                fresh_campaign = get_campaign_by_key(log_conn, campaign_key=campaign_key)
                if fresh_campaign is None:
                    return
                _log_event(
                    log_conn,
                    campaign=fresh_campaign,
                    event="send_emails",
                    success=success,
                    message=message,
                )
                log_conn.commit()

        # Send outside transaction
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

            # Filter out excluded recipients
            excluded_emails = set(
                str(r["email"]) for r in list_excluded_recipients_for_campaign(conn, campaign_id=int(campaign["id"]))
            )
            inv_rows = [r for r in inv_rows if str(r["email"]) not in excluded_emails]
            if not inv_rows:
                flash("All recipients have been excluded from this campaign.", "error")
                return redirect(url_for("master_view", campaign_key=campaign_key))

            email_from = (campaign["email_from"] or "").strip()
            email_subject = (campaign["email_subject"] or "").strip()
            email_html = (campaign["email_html"] or "").strip()
            base_url = (campaign["email_base_url"] or "http://127.0.0.1:5055").strip()
            base_url_norm = base_url.rstrip("/")
            cloud_base_url = (os.environ.get("CLOUDFLARE_STUDY_BASE_URL") or "").strip().rstrip("/")
            is_local_base = base_url_norm.startswith("http://127.0.0.1") or base_url_norm.startswith("http://localhost")
            admin_mode = _admin_mode_from_conn(conn)

            if not email_from or not email_subject or not email_html:
                flash("Missing email settings: email_from, email_subject, and email_html are required.", "error")
                return redirect(url_for("master_view", campaign_key=campaign_key))

            if admin_mode == "local" and not is_local_base:
                flash(
                    "In Local mode, email_base_url should be local (e.g. http://127.0.0.1:5055). Preview links may be invalid otherwise.",
                    "warning",
                )

            if admin_mode == "cloud":
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

            name_map = _recipient_name_map(conn, emails=[str(r["email"]) for r in inv_rows])

            previews: list[dict[str, str]] = []
            for r in inv_rows:
                # inv_rows can contain sqlite3.Row or dict (we sometimes rewrite rows when cloud tokens are used).
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
                rendered_html = _render_email_preview(html=email_html, variables=variables)
                # Generate plain text version for preview (same logic used when sending)
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
            admin_mode=admin_mode,
            email_from=email_from,
            email_subject=email_subject,
            previews=previews,
        )

    @app.get("/campaigns/<campaign_key>/recipients")
    def campaign_recipients(campaign_key: str) -> str:
        """
        View and manage recipients for this campaign.
        Shows pending recipients (not excluded, not yet submitted) and allows exclusion.
        """
        with db.connect() as conn:
            campaign = get_campaign_by_key(conn, campaign_key=campaign_key)
            if campaign is None:
                flash("Campaign not found", "error")
                return redirect(url_for("home"))
            
            pending = list_pending_recipients_for_campaign(conn, campaign_id=int(campaign["id"]))
            excluded = list_excluded_recipients_for_campaign(conn, campaign_id=int(campaign["id"]))
            counts = count_pending_recipients(conn, campaign_id=int(campaign["id"]))
            
            # Parse strata_json for display
            def parse_recipient(row: sqlite3.Row) -> dict[str, Any]:
                strata = {}
                try:
                    strata = json.loads(row["strata_json"]) if row["strata_json"] else {}
                except Exception:
                    pass
                row_keys = row.keys()
                return {
                    "email": row["email"],
                    "firstname": strata.get("firstname", ""),
                    "lastname": strata.get("lastname", ""),
                    "token": row["token"] if "token" in row_keys else None,
                    "opened_at": row["opened_at"] if "opened_at" in row_keys else None,
                    "questionnaire_hash": row["questionnaire_hash"] if "questionnaire_hash" in row_keys else None,
                    "has_submitted": bool(row["has_submitted"]) if "has_submitted" in row_keys else False,
                }
            
            def parse_excluded(row: sqlite3.Row) -> dict[str, Any]:
                strata = {}
                try:
                    strata = json.loads(row["strata_json"]) if row["strata_json"] else {}
                except Exception:
                    pass
                return {
                    "email": row["email"],
                    "firstname": strata.get("firstname", ""),
                    "lastname": strata.get("lastname", ""),
                    "excluded_at": row["excluded_at"],
                }
            
            pending_parsed = [parse_recipient(r) for r in pending]
            excluded_parsed = [parse_excluded(r) for r in excluded]
            
            # Split pending into: not_sent (no submission) and submitted
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
        """
        Exclude a recipient from this campaign.
        """
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
                _log_event(conn, campaign=campaign, event="exclude_recipient", success=True, message=f"Excluded {email}")
                flash(f"Excluded {email} from this campaign", "success")
            else:
                flash(f"{email} was already excluded", "warning")
            conn.commit()
        
        return redirect(url_for("campaign_recipients", campaign_key=campaign_key))

    @app.post("/campaigns/<campaign_key>/recipients/restore")
    def restore_recipient(campaign_key: str) -> Response:
        """
        Restore a previously excluded recipient to this campaign.
        """
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
                _log_event(conn, campaign=campaign, event="restore_recipient", success=True, message=f"Restored {email}")
                flash(f"Restored {email} to this campaign", "success")
            else:
                flash(f"{email} was not excluded", "warning")
            conn.commit()
        
        return redirect(url_for("campaign_recipients", campaign_key=campaign_key))

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
        return render_template(
            "preview.html",
            campaign=campaign,
            email=row["email"],
            qjson=qjson,
            layout_config=_normalize_layout_config(str(campaign["layout_yaml"] or DEFAULT_LAYOUT_YAML)),
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


