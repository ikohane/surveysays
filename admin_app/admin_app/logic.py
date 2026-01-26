from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from typing import Any

from qgen.qpicker.base import QuestionUnit, build_questionnaire_from_units

from .db import (
    DEFAULT_LAYOUT_YAML,
    get_setting,
    get_cloud_last_synced_at,
    set_cloud_last_synced_at,
    upsert_submission_from_cloud,
    insert_submission_answer,
    list_assignments_for_token,
    list_question_items_with_stats,
    insert_assignment,
    increment_assigned_count,
    get_question_item,
    save_invitation_snapshot,
    record_event,
)
from .utils import (
    parse_simple_yaml_to_obj,
    cloud_get_json,
    parse_json_obj,
)


def admin_mode_from_conn(conn: sqlite3.Connection) -> str:
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


def normalize_layout_config(layout_yaml: str | None) -> dict[str, Any]:
    """
    Parse YAML and normalize to a small, stable config object.
    """
    obj = parse_simple_yaml_to_obj(layout_yaml or "")
    if not obj:
        obj = parse_simple_yaml_to_obj(DEFAULT_LAYOUT_YAML)
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


# Default email template in YAML format
DEFAULT_EMAIL_YAML = """\
from: "Study Team <study@hvp.global>"
subject: "You're invited to participate"
base_url: "http://127.0.0.1:5055"
html: |
  <p>Dear {{{RECIPIENT_FIRST_NAME}}},</p>

  <p>You are invited to participate in our study: <strong>{{{CAMPAIGN_TITLE}}}</strong>.</p>

  <p><a href="{{{SURVEY_LINK}}}">Click here to begin the survey</a></p>

  <p>Or copy and paste this link into your browser:<br/>
  {{{SURVEY_LINK}}}</p>

  <p>Thank you for your participation.</p>
"""


def normalize_email_config(email_yaml: str | None) -> dict[str, Any]:
    """
    Parse email YAML and normalize to a stable config object.
    Returns dict with keys: from_email, subject, base_url, html
    """
    obj = parse_simple_yaml_to_obj(email_yaml or "")
    if not obj:
        obj = parse_simple_yaml_to_obj(DEFAULT_EMAIL_YAML)
    return {
        "from_email": str(obj.get("from", "") or "").strip(),
        "subject": str(obj.get("subject", "") or "").strip(),
        "base_url": str(obj.get("base_url", "http://127.0.0.1:5055") or "http://127.0.0.1:5055").strip(),
        "html": str(obj.get("html", "") or "").strip(),
    }


def log_event(
    conn: sqlite3.Connection,
    *,
    campaign: sqlite3.Row | None,
    event: str,
    success: bool,
    message: str | None = None,
) -> None:
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


def csv_escape(s: str) -> str:
    """
    Minimal CSV escaping: wrap fields containing commas/quotes/newlines, and double quotes.
    """
    s = str(s)
    if any(ch in s for ch in [",", "\"", "\n", "\r"]):
        return "\"" + s.replace("\"", "\"\"") + "\""
    return s


def recipient_name_map(conn: sqlite3.Connection, *, emails: list[str]) -> dict[str, dict[str, str]]:
    """
    Returns mapping email -> {firstname, lastname} from recipients.strata_json.
    """
    if not emails:
        return {}
    
    # Build parameterized query safely
    placeholders = ",".join(["?"] * len(emails))
    rows = conn.execute(
        f"SELECT email, strata_json FROM recipients WHERE email IN ({placeholders})",
        emails,
    ).fetchall()
    out: dict[str, dict[str, str]] = {}
    for r in rows:
        strata = parse_json_obj(r["strata_json"] if r["strata_json"] else None)
        out[str(r["email"])] = {
            "firstname": str(strata.get("firstname") or ""),
            "lastname": str(strata.get("lastname") or ""),
        }
    return out


def maybe_sync_cloud_submissions(
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

    injected = (os.environ.get("SURVEYSAYS_TEST_CLOUD_EXPORT_JSON") or "").strip()
    if injected:
        export = json.loads(injected)
    else:
        export = cloud_get_json(
            url=f"{cloud_base_url}/api/admin/export/{campaign_key}",
            bearer_token=cloud_admin_token,
            timeout_sec=20,
        )

    subs = export.get("submissions") if isinstance(export, dict) else None
    subs_list = subs if isinstance(subs, list) else []

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

        upsert_submission_from_cloud(
            conn,
            campaign_id=campaign_id,
            token=token,
            email=email,
            answers_json=answers_json,
            submitted_at=submitted_at,
        )
        n_upserted += 1

        try:
            answers_obj = json.loads(answers_json)
        except Exception:
            continue
        answers_map = answers_obj.get("answers") if isinstance(answers_obj, dict) else None
        if not isinstance(answers_map, dict):
            continue

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


def stable_tiebreak_int(token: str, item_id: str) -> int:
    h = hashlib.sha256(f"{token}|{item_id}".encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big", signed=False)


def assign_on_open(*, conn: sqlite3.Connection, campaign_row: sqlite3.Row, invitation_row: sqlite3.Row) -> dict[str, Any]:
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
    # sqlite3.Row doesn't support 'in' operator reliably; access with default
    try:
        k = int(campaign_row["k"])
    except (KeyError, IndexError):
        k = 1

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
            scored.append((assigned_count, stable_tiebreak_int(token, item_id), item_id))
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


def validate_answers_against_snapshot(*, qjson: dict[str, Any], answers: dict[str, str]) -> None:
    """
    Enforces MVP response contract (same as local Flask behavior):
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

    for k in answers.keys():
        if k not in answerable:
            raise ValueError(f"Answer provided for unknown or non-answerable block id '{k}'")

    missing = sorted([rid for rid in required_ids if rid not in answers or not str(answers.get(rid, "")).strip()])
    if missing:
        raise ValueError(f"Missing answers for required blocks: {', '.join(missing)}")

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


