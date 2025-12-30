from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from qgen.hashing import questionnaire_hash
from qgen.contracts import CaseRow, RecipientRow
from qgen.templates_contracts import TemplateRow


def _utc_now_sql() -> str:
    return "CURRENT_TIMESTAMP"


SCHEMA_SQL = f"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS campaigns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_key TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  seed INTEGER NOT NULL,
  questionnaire_version INTEGER NOT NULL,
  email_from TEXT,
  email_subject TEXT,
  email_html TEXT,
  email_template_id TEXT,
  email_base_url TEXT,
  created_at TEXT NOT NULL DEFAULT ({_utc_now_sql()})
);

CREATE TABLE IF NOT EXISTS invitations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id INTEGER NOT NULL,
  email TEXT NOT NULL,
  token TEXT NOT NULL UNIQUE,
  questionnaire_json TEXT,
  questionnaire_hash TEXT,
  opened_at TEXT,
  created_at TEXT NOT NULL DEFAULT ({_utc_now_sql()}),
  FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_invitations_campaign_id ON invitations(campaign_id);
CREATE INDEX IF NOT EXISTS idx_invitations_email ON invitations(email);
CREATE INDEX IF NOT EXISTS idx_invitations_token ON invitations(token);
CREATE UNIQUE INDEX IF NOT EXISTS idx_invitations_campaign_email ON invitations(campaign_id, email);

CREATE TABLE IF NOT EXISTS templates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  template_id TEXT NOT NULL UNIQUE,
  vignette_template TEXT NOT NULL,
  prompt_template TEXT NOT NULL,
  choices_json TEXT NOT NULL,
  tags_json TEXT NOT NULL,
  rules_yaml TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT ({_utc_now_sql()})
);

CREATE TABLE IF NOT EXISTS cases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  case_id TEXT NOT NULL UNIQUE,
  vignette TEXT NOT NULL,
  prompt TEXT NOT NULL,
  choices_json TEXT NOT NULL,
  tags_json TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT ({_utc_now_sql()})
);

CREATE TABLE IF NOT EXISTS recipients (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE,
  strata_json TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT ({_utc_now_sql()})
);

CREATE TABLE IF NOT EXISTS question_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id INTEGER NOT NULL,
  item_id TEXT NOT NULL,
  source_kind TEXT NOT NULL, -- 'case' | 'template'
  source_id TEXT NOT NULL,
  vignette TEXT NOT NULL,
  prompt TEXT NOT NULL,
  choices_json TEXT NOT NULL,
  tags_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT ({_utc_now_sql()}),
  UNIQUE (campaign_id, item_id),
  FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_question_items_campaign_id ON question_items(campaign_id);

CREATE TABLE IF NOT EXISTS question_stats (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id INTEGER NOT NULL,
  item_id TEXT NOT NULL,
  assigned_count INTEGER NOT NULL DEFAULT 0,
  submitted_count INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT ({_utc_now_sql()}),
  UNIQUE (campaign_id, item_id),
  FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_question_stats_campaign_id ON question_stats(campaign_id);

CREATE TABLE IF NOT EXISTS respondent_assignments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id INTEGER NOT NULL,
  token TEXT NOT NULL,
  item_id TEXT NOT NULL,
  position INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT ({_utc_now_sql()}),
  UNIQUE (campaign_id, token, position),
  FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_respondent_assignments_campaign_id ON respondent_assignments(campaign_id);
CREATE INDEX IF NOT EXISTS idx_respondent_assignments_token ON respondent_assignments(token);

CREATE TABLE IF NOT EXISTS submissions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id INTEGER NOT NULL,
  token TEXT NOT NULL UNIQUE,
  email TEXT NOT NULL,
  answers_json TEXT NOT NULL,
  submitted_at TEXT NOT NULL DEFAULT ({_utc_now_sql()}),
  FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_submissions_campaign_id ON submissions(campaign_id);
CREATE INDEX IF NOT EXISTS idx_submissions_token ON submissions(token);

CREATE TABLE IF NOT EXISTS submission_answers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id INTEGER NOT NULL,
  token TEXT NOT NULL,
  block_id TEXT NOT NULL,
  block_type TEXT NOT NULL,
  value_text TEXT,
  value_choice_id TEXT,
  created_at TEXT NOT NULL DEFAULT ({_utc_now_sql()}),
  UNIQUE (campaign_id, token, block_id),
  FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_submission_answers_campaign_id ON submission_answers(campaign_id);
CREATE INDEX IF NOT EXISTS idx_submission_answers_token ON submission_answers(token);

CREATE TABLE IF NOT EXISTS invitation_variants (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id INTEGER NOT NULL,
  email TEXT NOT NULL,
  case_id TEXT NOT NULL,
  questionnaire_json TEXT NOT NULL,
  questionnaire_hash TEXT NOT NULL,
  metadata_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT ({_utc_now_sql()}),
  FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_invitation_variants_campaign_id ON invitation_variants(campaign_id);
CREATE INDEX IF NOT EXISTS idx_invitation_variants_email ON invitation_variants(email);
CREATE INDEX IF NOT EXISTS idx_invitation_variants_hash ON invitation_variants(questionnaire_hash);
"""


class Db:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)
            # Lightweight migrations for existing DBs
            _ensure_campaign_columns(conn)
            _ensure_invitations_columns(conn)
            _ensure_question_stats_columns(conn)
            _ensure_submission_tables(conn)


def _try_add_column(conn: sqlite3.Connection, table: str, column_def: str) -> None:
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")
    except sqlite3.OperationalError:
        # Column likely already exists
        pass


def _ensure_campaign_columns(conn: sqlite3.Connection) -> None:
    _try_add_column(conn, "campaigns", "picker_strategy TEXT NOT NULL DEFAULT 'pick_k_cases'")
    _try_add_column(conn, "campaigns", "k INTEGER NOT NULL DEFAULT 1")
    _try_add_column(conn, "campaigns", "param_vector_json TEXT")
    _try_add_column(conn, "campaigns", "email_from TEXT")
    _try_add_column(conn, "campaigns", "email_subject TEXT")
    _try_add_column(conn, "campaigns", "email_html TEXT")
    _try_add_column(conn, "campaigns", "email_template_id TEXT")
    _try_add_column(conn, "campaigns", "email_base_url TEXT")


def _ensure_invitations_columns(conn: sqlite3.Connection) -> None:
    # Older DBs may have invitations without these columns; make add-only migrations safe.
    _try_add_column(conn, "invitations", "questionnaire_json TEXT")
    _try_add_column(conn, "invitations", "questionnaire_hash TEXT")
    _try_add_column(conn, "invitations", "opened_at TEXT")


def _ensure_question_stats_columns(conn: sqlite3.Connection) -> None:
    _try_add_column(conn, "question_stats", "assigned_count INTEGER NOT NULL DEFAULT 0")
    _try_add_column(conn, "question_stats", "submitted_count INTEGER NOT NULL DEFAULT 0")


def _ensure_submission_tables(conn: sqlite3.Connection) -> None:
    # Create tables if they don't exist (CREATE TABLE IF NOT EXISTS is idempotent)
    conn.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS submissions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          campaign_id INTEGER NOT NULL,
          token TEXT NOT NULL UNIQUE,
          email TEXT NOT NULL,
          answers_json TEXT NOT NULL,
          submitted_at TEXT NOT NULL DEFAULT ({_utc_now_sql()}),
          FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_submissions_campaign_id ON submissions(campaign_id);
        CREATE INDEX IF NOT EXISTS idx_submissions_token ON submissions(token);

        CREATE TABLE IF NOT EXISTS submission_answers (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          campaign_id INTEGER NOT NULL,
          token TEXT NOT NULL,
          block_id TEXT NOT NULL,
          block_type TEXT NOT NULL,
          value_text TEXT,
          value_choice_id TEXT,
          created_at TEXT NOT NULL DEFAULT ({_utc_now_sql()}),
          UNIQUE (campaign_id, token, block_id),
          FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_submission_answers_campaign_id ON submission_answers(campaign_id);
        CREATE INDEX IF NOT EXISTS idx_submission_answers_token ON submission_answers(token);
        """
    )


# -----------------------------
# Campaigns
# -----------------------------


def upsert_campaign(
    conn: sqlite3.Connection,
    *,
    campaign_key: str,
    title: str,
    seed: int,
    questionnaire_version: int,
) -> int:
    conn.execute(
        """
        INSERT INTO campaigns (campaign_key, title, seed, questionnaire_version)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(campaign_key) DO UPDATE SET
          title = excluded.title,
          seed = excluded.seed,
          questionnaire_version = excluded.questionnaire_version
        """,
        (campaign_key, title, seed, questionnaire_version),
    )
    row = conn.execute(
        "SELECT id FROM campaigns WHERE campaign_key = ?",
        (campaign_key,),
    ).fetchone()
    assert row is not None
    return int(row["id"])


def get_campaign_by_key(conn: sqlite3.Connection, *, campaign_key: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM campaigns WHERE campaign_key = ?",
        (campaign_key,),
    ).fetchone()


def list_campaigns(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM campaigns ORDER BY created_at DESC").fetchall())


# -----------------------------
# Imports: cases & recipients
# -----------------------------


def upsert_cases(conn: sqlite3.Connection, cases: Iterable[CaseRow]) -> int:
    n = 0
    for c in cases:
        conn.execute(
            """
            INSERT INTO cases (case_id, vignette, prompt, choices_json, tags_json, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(case_id) DO UPDATE SET
              vignette = excluded.vignette,
              prompt = excluded.prompt,
              choices_json = excluded.choices_json,
              tags_json = excluded.tags_json,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                c.case_id,
                c.vignette,
                c.prompt,
                json.dumps(c.choices, ensure_ascii=False),
                json.dumps(c.tags, ensure_ascii=False),
            ),
        )
        n += 1
    return n


def upsert_recipients(conn: sqlite3.Connection, recipients: Iterable[RecipientRow]) -> int:
    n = 0
    for r in recipients:
        conn.execute(
            """
            INSERT INTO recipients (email, strata_json, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(email) DO UPDATE SET
              strata_json = excluded.strata_json,
              updated_at = CURRENT_TIMESTAMP
            """,
            (r.email, json.dumps(r.strata, ensure_ascii=False)),
        )
        n += 1
    return n


def load_cases(conn: sqlite3.Connection) -> list[CaseRow]:
    rows = conn.execute("SELECT * FROM cases ORDER BY case_id").fetchall()
    out: list[CaseRow] = []
    for row in rows:
        out.append(
            CaseRow(
                case_id=row["case_id"],
                vignette=row["vignette"],
                prompt=row["prompt"],
                choices=json.loads(row["choices_json"]),
                tags=json.loads(row["tags_json"]),
            )
        )
    return out


def load_recipients(conn: sqlite3.Connection) -> list[RecipientRow]:
    rows = conn.execute("SELECT * FROM recipients ORDER BY email").fetchall()
    out: list[RecipientRow] = []
    for row in rows:
        out.append(RecipientRow(email=row["email"], strata=json.loads(row["strata_json"])))
    return out


# -----------------------------
# Templates
# -----------------------------


def upsert_templates(conn: sqlite3.Connection, templates: Iterable[TemplateRow]) -> int:
    n = 0
    for t in templates:
        conn.execute(
            """
            INSERT INTO templates
              (template_id, vignette_template, prompt_template, choices_json, tags_json, rules_yaml, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(template_id) DO UPDATE SET
              vignette_template = excluded.vignette_template,
              prompt_template = excluded.prompt_template,
              choices_json = excluded.choices_json,
              tags_json = excluded.tags_json,
              rules_yaml = excluded.rules_yaml,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                t.template_id,
                t.vignette_template,
                t.prompt_template,
                json.dumps(t.choices, ensure_ascii=False),
                json.dumps(t.tags, ensure_ascii=False),
                json.dumps(t.rules, ensure_ascii=False),  # stored canonicalized as JSON string
            ),
        )
        n += 1
    return n


def load_templates(conn: sqlite3.Connection) -> list[TemplateRow]:
    rows = conn.execute("SELECT * FROM templates ORDER BY template_id").fetchall()
    out: list[TemplateRow] = []
    for row in rows:
        # rules_yaml column stores JSON-serialized dict to keep it stable even if source was YAML
        try:
            rules = json.loads(row["rules_yaml"]) if row["rules_yaml"] else {}
        except Exception:
            rules = {}
        out.append(
            TemplateRow(
                template_id=row["template_id"],
                vignette_template=row["vignette_template"],
                prompt_template=row["prompt_template"],
                choices=json.loads(row["choices_json"]),
                tags=json.loads(row["tags_json"]),
                rules=rules,
            )
        )
    return out


# -----------------------------
# Generated variants
# -----------------------------


def clear_variants_for_campaign(conn: sqlite3.Connection, *, campaign_id: int) -> None:
    conn.execute("DELETE FROM invitation_variants WHERE campaign_id = ?", (campaign_id,))


def insert_variants(
    conn: sqlite3.Connection,
    *,
    campaign_id: int,
    variants: list[dict[str, Any]],
) -> int:
    """
    variants: list of dicts shaped like BulkInvitation (email, questionnaireJson, metadata, ...)
    """
    n = 0
    for inv in variants:
        qjson = inv["questionnaireJson"]
        meta = inv.get("metadata") or {}
        conn.execute(
            """
            INSERT INTO invitation_variants
              (campaign_id, email, case_id, questionnaire_json, questionnaire_hash, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                campaign_id,
                inv["email"],
                meta.get("caseId", ""),
                json.dumps(qjson, ensure_ascii=False),
                meta.get("questionnaireHash") or "",
                json.dumps(meta, ensure_ascii=False),
            ),
        )
        n += 1
    return n


def populate_invitations_from_variants(conn: sqlite3.Connection, *, campaign_id: int) -> int:
    """
    For offline campaigns, create (campaign_id,email)->token invitations and snapshot the pre-generated questionnaire.

    - Keeps existing tokens stable if invitation already exists.
    - Writes questionnaire_json + questionnaire_hash onto invitations.
    Returns number of invitations processed.
    """
    rows = conn.execute(
        """
        SELECT email, questionnaire_json, questionnaire_hash
        FROM invitation_variants
        WHERE campaign_id = ?
        ORDER BY email
        """,
        (campaign_id,),
    ).fetchall()
    n = 0
    for r in rows:
        token = secrets.token_urlsafe(18)
        conn.execute(
            """
            INSERT INTO invitations (campaign_id, email, token, questionnaire_json, questionnaire_hash)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(campaign_id, email) DO UPDATE SET
              questionnaire_json = excluded.questionnaire_json,
              questionnaire_hash = excluded.questionnaire_hash
            """,
            (campaign_id, r["email"], token, r["questionnaire_json"], r["questionnaire_hash"]),
        )
        n += 1
    return n


def list_variants_for_campaign(conn: sqlite3.Connection, *, campaign_id: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT *
            FROM invitation_variants
            WHERE campaign_id = ?
            ORDER BY email
            """,
            (campaign_id,),
        ).fetchall()
    )


def variant_counts(conn: sqlite3.Connection, *, campaign_id: int) -> dict[str, int]:
    row = conn.execute(
        """
        SELECT
          COUNT(*) AS total,
          COUNT(DISTINCT questionnaire_hash) AS distinct_hashes
        FROM invitation_variants
        WHERE campaign_id = ?
        """,
        (campaign_id,),
    ).fetchone()
    assert row is not None
    return {"total": int(row["total"]), "distinct_hashes": int(row["distinct_hashes"])}


def to_jsonable_row(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def to_jsonable_campaign(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def to_jsonable_case(case: CaseRow) -> dict[str, Any]:
    return asdict(case)


def to_jsonable_recipient(rec: RecipientRow) -> dict[str, Any]:
    return asdict(rec)


# -----------------------------
# Online assignment: invitations / question bank
# -----------------------------


def create_invitations_for_campaign(
    conn: sqlite3.Connection,
    *,
    campaign_id: int,
    recipients: Iterable[RecipientRow],
) -> int:
    """
    Creates one invitation per (campaign_id, email) if missing. Tokens are generated once and persisted.
    Returns number of *new* invitations created.
    """
    n_new = 0
    for r in recipients:
        existing = conn.execute(
            "SELECT id FROM invitations WHERE campaign_id = ? AND email = ?",
            (campaign_id, r.email),
        ).fetchone()
        if existing is not None:
            continue
        token = secrets.token_urlsafe(18)
        conn.execute(
            """
            INSERT INTO invitations (campaign_id, email, token)
            VALUES (?, ?, ?)
            """,
            (campaign_id, r.email, token),
        )
        n_new += 1
    return n_new


def list_invitations_for_campaign(conn: sqlite3.Connection, *, campaign_id: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT *
            FROM invitations
            WHERE campaign_id = ?
            ORDER BY email
            """,
            (campaign_id,),
        ).fetchall()
    )


def get_invitation_by_token(conn: sqlite3.Connection, *, token: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM invitations WHERE token = ?", (token,)).fetchone()


def mark_invitation_opened(conn: sqlite3.Connection, *, token: str) -> None:
    conn.execute(
        """
        UPDATE invitations
        SET opened_at = COALESCE(opened_at, CURRENT_TIMESTAMP)
        WHERE token = ?
        """,
        (token,),
    )


def save_invitation_snapshot(
    conn: sqlite3.Connection,
    *,
    token: str,
    questionnaire_json_obj: dict[str, Any],
) -> None:
    qh = questionnaire_hash(questionnaire_json_obj)
    conn.execute(
        """
        UPDATE invitations
        SET questionnaire_json = ?, questionnaire_hash = ?
        WHERE token = ?
        """,
        (json.dumps(questionnaire_json_obj, ensure_ascii=False), qh, token),
    )


def clear_question_bank(conn: sqlite3.Connection, *, campaign_id: int) -> None:
    conn.execute("DELETE FROM respondent_assignments WHERE campaign_id = ?", (campaign_id,))
    conn.execute("DELETE FROM question_stats WHERE campaign_id = ?", (campaign_id,))
    conn.execute("DELETE FROM question_items WHERE campaign_id = ?", (campaign_id,))


def upsert_question_items_from_cases(conn: sqlite3.Connection, *, campaign_id: int, cases: Iterable[CaseRow]) -> int:
    """
    Populates question_items with a stable item_id of the form 'case:<case_id>'.
    Also ensures there is a matching question_stats row per item.
    """
    n = 0
    for c in cases:
        item_id = f"case:{c.case_id}"
        conn.execute(
            """
            INSERT INTO question_items
              (campaign_id, item_id, source_kind, source_id, vignette, prompt, choices_json, tags_json)
            VALUES (?, ?, 'case', ?, ?, ?, ?, ?)
            ON CONFLICT(campaign_id, item_id) DO UPDATE SET
              source_kind = excluded.source_kind,
              source_id = excluded.source_id,
              vignette = excluded.vignette,
              prompt = excluded.prompt,
              choices_json = excluded.choices_json,
              tags_json = excluded.tags_json
            """,
            (
                campaign_id,
                item_id,
                c.case_id,
                c.vignette,
                c.prompt,
                json.dumps(c.choices, ensure_ascii=False),
                json.dumps(c.tags, ensure_ascii=False),
            ),
        )
        conn.execute(
            """
            INSERT INTO question_stats (campaign_id, item_id)
            VALUES (?, ?)
            ON CONFLICT(campaign_id, item_id) DO NOTHING
            """,
            (campaign_id, item_id),
        )
        n += 1
    return n


def list_question_items_with_stats(conn: sqlite3.Connection, *, campaign_id: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT
              qi.item_id,
              qi.source_kind,
              qi.source_id,
              qs.assigned_count,
              qs.submitted_count,
              qi.created_at
            FROM question_items qi
            LEFT JOIN question_stats qs
              ON qs.campaign_id = qi.campaign_id AND qs.item_id = qi.item_id
            WHERE qi.campaign_id = ?
            ORDER BY qs.assigned_count ASC, qi.item_id ASC
            """,
            (campaign_id,),
        ).fetchall()
    )


def get_question_item(conn: sqlite3.Connection, *, campaign_id: int, item_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM question_items
        WHERE campaign_id = ? AND item_id = ?
        """,
        (campaign_id, item_id),
    ).fetchone()


def list_assignments_for_token(conn: sqlite3.Connection, *, campaign_id: int, token: str) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT *
            FROM respondent_assignments
            WHERE campaign_id = ? AND token = ?
            ORDER BY position ASC
            """,
            (campaign_id, token),
        ).fetchall()
    )


def insert_assignment(conn: sqlite3.Connection, *, campaign_id: int, token: str, item_id: str, position: int) -> None:
    conn.execute(
        """
        INSERT INTO respondent_assignments (campaign_id, token, item_id, position)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(campaign_id, token, position) DO NOTHING
        """,
        (campaign_id, token, item_id, position),
    )


def increment_assigned_count(conn: sqlite3.Connection, *, campaign_id: int, item_id: str) -> None:
    conn.execute(
        """
        UPDATE question_stats
        SET assigned_count = assigned_count + 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE campaign_id = ? AND item_id = ?
        """,
        (campaign_id, item_id),
    )


def increment_submitted_count(conn: sqlite3.Connection, *, campaign_id: int, item_id: str) -> None:
    conn.execute(
        """
        UPDATE question_stats
        SET submitted_count = submitted_count + 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE campaign_id = ? AND item_id = ?
        """,
        (campaign_id, item_id),
    )


def has_submission(conn: sqlite3.Connection, *, token: str) -> bool:
    row = conn.execute("SELECT 1 FROM submissions WHERE token = ? LIMIT 1", (token,)).fetchone()
    return row is not None


def insert_submission(
    conn: sqlite3.Connection,
    *,
    campaign_id: int,
    token: str,
    email: str,
    answers: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO submissions (campaign_id, token, email, answers_json)
        VALUES (?, ?, ?, ?)
        """,
        (campaign_id, token, email, json.dumps(answers, ensure_ascii=False)),
    )


def insert_submission_answer(
    conn: sqlite3.Connection,
    *,
    campaign_id: int,
    token: str,
    block_id: str,
    block_type: str,
    value_text: str | None,
    value_choice_id: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO submission_answers
          (campaign_id, token, block_id, block_type, value_text, value_choice_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (campaign_id, token, block_id, block_type, value_text, value_choice_id),
    )


def submission_cohort_counts(conn: sqlite3.Connection, *, campaign_id: int) -> dict[str, int]:
    row = conn.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM invitations WHERE campaign_id = ?) AS invited,
          (SELECT COUNT(*) FROM invitations WHERE campaign_id = ? AND opened_at IS NOT NULL) AS opened,
          (SELECT COUNT(*) FROM invitations WHERE campaign_id = ? AND questionnaire_hash IS NOT NULL) AS assigned,
          (SELECT COUNT(*) FROM submissions WHERE campaign_id = ?) AS submitted
        """,
        (campaign_id, campaign_id, campaign_id, campaign_id),
    ).fetchone()
    assert row is not None
    return {
        "invited": int(row["invited"]),
        "opened": int(row["opened"]),
        "assigned": int(row["assigned"]),
        "submitted": int(row["submitted"]),
    }


def report_rows(
    conn: sqlite3.Connection,
    *,
    campaign_id: int,
    kind: str,
) -> list[sqlite3.Row]:
    """
    kind in: invited_not_submitted | opened_not_submitted | assigned_not_submitted | submitted
    """
    if kind == "submitted":
        return list(
            conn.execute(
                """
                SELECT
                  i.email,
                  i.token,
                  i.opened_at,
                  i.questionnaire_hash,
                  s.submitted_at
                FROM submissions s
                JOIN invitations i ON i.campaign_id = s.campaign_id AND i.token = s.token
                WHERE s.campaign_id = ?
                ORDER BY s.submitted_at DESC
                """,
                (campaign_id,),
            ).fetchall()
        )

    where_extra = ""
    if kind == "invited_not_submitted":
        where_extra = "1=1"
    elif kind == "opened_not_submitted":
        where_extra = "i.opened_at IS NOT NULL"
    elif kind == "assigned_not_submitted":
        where_extra = "i.questionnaire_hash IS NOT NULL"
    else:
        raise ValueError(f"Unknown report kind: {kind}")

    return list(
        conn.execute(
            f"""
            SELECT
              i.email,
              i.token,
              i.opened_at,
              i.questionnaire_hash,
              NULL AS submitted_at
            FROM invitations i
            LEFT JOIN submissions s ON s.campaign_id = i.campaign_id AND s.token = i.token
            WHERE i.campaign_id = ?
              AND s.token IS NULL
              AND ({where_extra})
            ORDER BY i.email
            """,
            (campaign_id,),
        ).fetchall()
    )


