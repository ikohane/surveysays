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

CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cloud_sync_state (
  campaign_id INTEGER NOT NULL,
  cloud_base_url TEXT NOT NULL,
  last_synced_at TEXT,
  PRIMARY KEY (campaign_id, cloud_base_url),
  FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS campaigns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_key TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  seed INTEGER NOT NULL,
  questionnaire_version INTEGER NOT NULL,
  layout_yaml TEXT,
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

CREATE TABLE IF NOT EXISTS event_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id INTEGER,
  campaign_key TEXT,
  event TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('success','failure')),
  message TEXT,
  created_at TEXT NOT NULL DEFAULT ({_utc_now_sql()}),
  FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_event_log_campaign_id ON event_log(campaign_id);

CREATE TABLE IF NOT EXISTS campaign_recipient_exclusions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id INTEGER NOT NULL,
  email TEXT NOT NULL,
  excluded_at TEXT NOT NULL DEFAULT ({_utc_now_sql()}),
  UNIQUE (campaign_id, email),
  FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_campaign_recipient_exclusions_campaign_id ON campaign_recipient_exclusions(campaign_id);
CREATE INDEX IF NOT EXISTS idx_campaign_recipient_exclusions_email ON campaign_recipient_exclusions(email);

CREATE TABLE IF NOT EXISTS generation_waves (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id INTEGER NOT NULL,
  wave_number INTEGER NOT NULL,
  picker_strategy TEXT NOT NULL,
  k INTEGER NOT NULL,
  seed INTEGER NOT NULL,
  recipients_processed INTEGER NOT NULL,
  variants_created INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT ({_utc_now_sql()}),
  UNIQUE (campaign_id, wave_number),
  FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_generation_waves_campaign_id ON generation_waves(campaign_id);
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
            _ensure_settings_tables(conn)
            _ensure_campaign_columns(conn)
            _ensure_invitations_columns(conn)
            _ensure_question_stats_columns(conn)
            _ensure_submission_tables(conn)
            _ensure_cloud_tables(conn)
            _ensure_exclusions_table(conn)
            _ensure_generation_waves_table(conn)


def _try_add_column(conn: sqlite3.Connection, table: str, column_def: str) -> None:
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")
    except sqlite3.OperationalError:
        # Column likely already exists
        pass


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _ensure_settings_tables(conn: sqlite3.Connection) -> None:
    # Older DBs may not have app_settings.
    if not _table_exists(conn, "app_settings"):
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            )
            """
        )

    if not _table_exists(conn, "cloud_sync_state"):
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cloud_sync_state (
              campaign_id INTEGER NOT NULL,
              cloud_base_url TEXT NOT NULL,
              last_synced_at TEXT,
              PRIMARY KEY (campaign_id, cloud_base_url),
              FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
            )
            """
        )


def _ensure_campaign_columns(conn: sqlite3.Connection) -> None:
    _try_add_column(conn, "campaigns", "picker_strategy TEXT NOT NULL DEFAULT 'pick_k_cases'")
    _try_add_column(conn, "campaigns", "k INTEGER NOT NULL DEFAULT 1")
    _try_add_column(conn, "campaigns", "param_vector_json TEXT")
    _try_add_column(conn, "campaigns", "layout_yaml TEXT")
    _try_add_column(conn, "campaigns", "email_from TEXT")
    _try_add_column(conn, "campaigns", "email_subject TEXT")
    _try_add_column(conn, "campaigns", "email_html TEXT")
    _try_add_column(conn, "campaigns", "email_template_id TEXT")
    _try_add_column(conn, "campaigns", "email_base_url TEXT")
    _try_add_column(conn, "campaigns", "email_yaml TEXT")


DEFAULT_LAYOUT_YAML = """version: 1
prompt_first: true
question_demarcation:
  style: card
  gap_px: 16
single_select:
  layout: cards
  selected_style: highlight
"""


def get_setting(conn: sqlite3.Connection, *, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    if not row:
        return None
    return str(row["value"])


def set_setting(conn: sqlite3.Connection, *, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO app_settings (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def get_cloud_last_synced_at(conn: sqlite3.Connection, *, campaign_id: int, cloud_base_url: str) -> str | None:
    row = conn.execute(
        "SELECT last_synced_at FROM cloud_sync_state WHERE campaign_id = ? AND cloud_base_url = ?",
        (campaign_id, cloud_base_url),
    ).fetchone()
    if not row:
        return None
    v = row["last_synced_at"]
    return str(v) if v else None


def set_cloud_last_synced_at(conn: sqlite3.Connection, *, campaign_id: int, cloud_base_url: str, last_synced_at: str) -> None:
    conn.execute(
        """
        INSERT INTO cloud_sync_state (campaign_id, cloud_base_url, last_synced_at)
        VALUES (?, ?, ?)
        ON CONFLICT(campaign_id, cloud_base_url) DO UPDATE SET
          last_synced_at = excluded.last_synced_at
        """,
        (campaign_id, cloud_base_url, last_synced_at),
    )


def _ensure_invitations_columns(conn: sqlite3.Connection) -> None:
    # Older DBs may have invitations without these columns; make add-only migrations safe.
    _try_add_column(conn, "invitations", "questionnaire_json TEXT")
    _try_add_column(conn, "invitations", "questionnaire_hash TEXT")
    _try_add_column(conn, "invitations", "opened_at TEXT")


def _ensure_question_stats_columns(conn: sqlite3.Connection) -> None:
    _try_add_column(conn, "question_stats", "assigned_count INTEGER NOT NULL DEFAULT 0")
    _try_add_column(conn, "question_stats", "submitted_count INTEGER NOT NULL DEFAULT 0")


def _ensure_cloud_tables(conn: sqlite3.Connection) -> None:
    # Ensure new tables exist.
    conn.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS cloud_pushes (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          campaign_id INTEGER NOT NULL,
          cloud_base_url TEXT NOT NULL,
          request_hash TEXT NOT NULL,
          response_json TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT ({_utc_now_sql()}),
          FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_cloud_pushes_campaign_id ON cloud_pushes(campaign_id);
        CREATE INDEX IF NOT EXISTS idx_cloud_pushes_created_at ON cloud_pushes(created_at);

        -- Legacy table retained for migration.
        CREATE TABLE IF NOT EXISTS cloud_uploads (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          campaign_id INTEGER NOT NULL,
          cloud_base_url TEXT NOT NULL,
          request_hash TEXT NOT NULL,
          response_json TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT ({_utc_now_sql()}),
          FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_cloud_uploads_campaign_id ON cloud_uploads(campaign_id);
        CREATE INDEX IF NOT EXISTS idx_cloud_uploads_created_at ON cloud_uploads(created_at);
        """
    )

    # Migrate tokens table to per-push (append-only) if needed.
    def _table_exists(name: str) -> bool:
        r = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        return r is not None

    def _table_has_column(table: str, col: str) -> bool:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(str(r["name"]) == col for r in rows)

    if not _table_exists("cloud_invitation_tokens"):
        conn.executescript(
            f"""
            CREATE TABLE cloud_invitation_tokens (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              push_id INTEGER NOT NULL,
              campaign_id INTEGER NOT NULL,
              cloud_base_url TEXT NOT NULL,
              email TEXT NOT NULL,
              cloud_token TEXT NOT NULL,
              uploaded_at TEXT NOT NULL DEFAULT ({_utc_now_sql()}),
              UNIQUE (push_id, email),
              FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE,
              FOREIGN KEY (push_id) REFERENCES cloud_pushes(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_cloud_invitation_tokens_campaign_id ON cloud_invitation_tokens(campaign_id);
            CREATE INDEX IF NOT EXISTS idx_cloud_invitation_tokens_email ON cloud_invitation_tokens(email);
            CREATE INDEX IF NOT EXISTS idx_cloud_invitation_tokens_push_id ON cloud_invitation_tokens(push_id);
            """
        )
        return

    if _table_has_column("cloud_invitation_tokens", "push_id"):
        # Already migrated.
        return

    # Legacy schema exists (unique per email); migrate to append-only.
    # Drop legacy indexes first: SQLite index names are global, and table rename keeps index names.
    conn.execute("DROP INDEX IF EXISTS idx_cloud_invitation_tokens_campaign_id")
    conn.execute("DROP INDEX IF EXISTS idx_cloud_invitation_tokens_email")
    conn.execute("DROP INDEX IF EXISTS idx_cloud_invitation_tokens_push_id")
    conn.execute("ALTER TABLE cloud_invitation_tokens RENAME TO cloud_invitation_tokens_legacy")
    conn.executescript(
        f"""
        CREATE TABLE cloud_invitation_tokens (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          push_id INTEGER NOT NULL,
          campaign_id INTEGER NOT NULL,
          cloud_base_url TEXT NOT NULL,
          email TEXT NOT NULL,
          cloud_token TEXT NOT NULL,
          uploaded_at TEXT NOT NULL DEFAULT ({_utc_now_sql()}),
          UNIQUE (push_id, email),
          FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE,
          FOREIGN KEY (push_id) REFERENCES cloud_pushes(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_cloud_invitation_tokens_campaign_id ON cloud_invitation_tokens(campaign_id);
        CREATE INDEX IF NOT EXISTS idx_cloud_invitation_tokens_email ON cloud_invitation_tokens(email);
        CREATE INDEX IF NOT EXISTS idx_cloud_invitation_tokens_push_id ON cloud_invitation_tokens(push_id);
        """
    )

    # Backfill: create one push per (campaign_id, cloud_base_url) using latest cloud_uploads row if present.
    latest_uploads = list(
        conn.execute(
            """
            SELECT cu.*
            FROM cloud_uploads cu
            JOIN (
              SELECT campaign_id, cloud_base_url, MAX(created_at) AS max_created_at
              FROM cloud_uploads
              GROUP BY campaign_id, cloud_base_url
            ) x
              ON x.campaign_id = cu.campaign_id
             AND x.cloud_base_url = cu.cloud_base_url
             AND x.max_created_at = cu.created_at
            """
        ).fetchall()
    )

    push_id_by_key: dict[tuple[int, str], int] = {}
    for r in latest_uploads:
        key = (int(r["campaign_id"]), str(r["cloud_base_url"]))
        res = conn.execute(
            """
            INSERT INTO cloud_pushes (campaign_id, cloud_base_url, request_hash, response_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                int(r["campaign_id"]),
                str(r["cloud_base_url"]),
                str(r["request_hash"]),
                str(r["response_json"]),
                str(r["created_at"]),
            ),
        )
        push_id_by_key[key] = int(res.lastrowid)

    # For any legacy tokens without a corresponding upload record, create a synthetic push.
    legacy_keys = list(
        conn.execute(
            """
            SELECT DISTINCT campaign_id, cloud_base_url
            FROM cloud_invitation_tokens_legacy
            """
        ).fetchall()
    )
    for r in legacy_keys:
        key = (int(r["campaign_id"]), str(r["cloud_base_url"]))
        if key in push_id_by_key:
            continue
        res = conn.execute(
            """
            INSERT INTO cloud_pushes (campaign_id, cloud_base_url, request_hash, response_json)
            VALUES (?, ?, ?, ?)
            """,
            (key[0], key[1], "legacy", "{}"),
        )
        push_id_by_key[key] = int(res.lastrowid)

    # Copy latest-known tokens into new table attached to the push for that campaign/base_url.
    rows = conn.execute(
        """
        SELECT campaign_id, cloud_base_url, email, cloud_token, uploaded_at
        FROM cloud_invitation_tokens_legacy
        """
    ).fetchall()
    for r in rows:
        key = (int(r["campaign_id"]), str(r["cloud_base_url"]))
        push_id = push_id_by_key.get(key)
        if not push_id:
            continue
        conn.execute(
            """
            INSERT INTO cloud_invitation_tokens (push_id, campaign_id, cloud_base_url, email, cloud_token, uploaded_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                push_id,
                int(r["campaign_id"]),
                str(r["cloud_base_url"]),
                str(r["email"]),
                str(r["cloud_token"]),
                str(r["uploaded_at"]),
            ),
        )


def _ensure_exclusions_table(conn: sqlite3.Connection) -> None:
    conn.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS campaign_recipient_exclusions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          campaign_id INTEGER NOT NULL,
          email TEXT NOT NULL,
          excluded_at TEXT NOT NULL DEFAULT ({_utc_now_sql()}),
          UNIQUE (campaign_id, email),
          FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_campaign_recipient_exclusions_campaign_id ON campaign_recipient_exclusions(campaign_id);
        CREATE INDEX IF NOT EXISTS idx_campaign_recipient_exclusions_email ON campaign_recipient_exclusions(email);
        """
    )


def _ensure_generation_waves_table(conn: sqlite3.Connection) -> None:
    """Ensure generation_waves table exists and add wave_id to invitation_variants."""
    conn.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS generation_waves (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          campaign_id INTEGER NOT NULL,
          wave_number INTEGER NOT NULL,
          picker_strategy TEXT NOT NULL,
          k INTEGER NOT NULL,
          seed INTEGER NOT NULL,
          recipients_processed INTEGER NOT NULL,
          variants_created INTEGER NOT NULL,
          created_at TEXT NOT NULL DEFAULT ({_utc_now_sql()}),
          UNIQUE (campaign_id, wave_number),
          FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_generation_waves_campaign_id ON generation_waves(campaign_id);
        """
    )
    # Add wave_id column to invitation_variants if it doesn't exist
    _try_add_column(conn, "invitation_variants", "wave_id INTEGER")
    # Try to create index (may fail if it already exists, that's OK)
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_invitation_variants_wave_id ON invitation_variants(wave_id)")
    except sqlite3.OperationalError:
        pass


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
        INSERT INTO campaigns (campaign_key, title, seed, questionnaire_version, layout_yaml)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(campaign_key) DO UPDATE SET
          title = excluded.title,
          seed = excluded.seed,
          questionnaire_version = excluded.questionnaire_version
        """,
        (campaign_key, title, seed, questionnaire_version, DEFAULT_LAYOUT_YAML),
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


def update_campaign_layout_yaml(conn: sqlite3.Connection, *, campaign_key: str, layout_yaml: str) -> None:
    conn.execute(
        "UPDATE campaigns SET layout_yaml = ? WHERE campaign_key = ?",
        (layout_yaml, campaign_key),
    )


def update_campaign_email_yaml(conn: sqlite3.Connection, *, campaign_key: str, email_yaml: str) -> None:
    conn.execute(
        "UPDATE campaigns SET email_yaml = ? WHERE campaign_key = ?",
        (email_yaml, campaign_key),
    )


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
    wave_id: int | None = None,
) -> int:
    """
    variants: list of dicts shaped like BulkInvitation (email, questionnaireJson, metadata, ...)
    wave_id: optional generation wave ID to track this batch
    """
    n = 0
    for inv in variants:
        qjson = inv["questionnaireJson"]
        meta = inv.get("metadata") or {}
        conn.execute(
            """
            INSERT INTO invitation_variants
              (campaign_id, email, case_id, questionnaire_json, questionnaire_hash, metadata_json, wave_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                campaign_id,
                inv["email"],
                meta.get("caseId", ""),
                json.dumps(qjson, ensure_ascii=False),
                meta.get("questionnaireHash") or "",
                json.dumps(meta, ensure_ascii=False),
                wave_id,
            ),
        )
        n += 1
    return n


def populate_invitations_from_variants(conn: sqlite3.Connection, *, campaign_id: int) -> int:
    """
    For offline campaigns, create (campaign_id,email)->token invitations and snapshot the pre-generated questionnaire.

    - Keeps existing tokens stable if invitation already exists.
    - Writes questionnaire_json + questionnaire_hash onto invitations.
    - Preserves wave_id for tracking generation waves.
    Returns number of invitations processed.
    """
    rows = conn.execute(
        """
        SELECT email, questionnaire_json, questionnaire_hash, wave_id
        FROM invitation_variants
        WHERE campaign_id = ?
        ORDER BY email
        """,
        (campaign_id,),
    ).fetchall()
    n = 0
    for r in rows:
        token = secrets.token_urlsafe(18)
        # Note: invitations table doesn't have wave_id column for offline campaigns
        # Wave tracking is maintained in invitation_variants table
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


# -----------------------------
# Generation waves
# -----------------------------


def insert_generation_wave(
    conn: sqlite3.Connection,
    *,
    campaign_id: int,
    wave_number: int,
    picker_strategy: str,
    k: int,
    seed: int,
    recipients_processed: int,
    variants_created: int,
) -> int:
    """Insert a new generation wave record and return its ID."""
    cur = conn.execute(
        """
        INSERT INTO generation_waves
          (campaign_id, wave_number, picker_strategy, k, seed, recipients_processed, variants_created)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (campaign_id, wave_number, picker_strategy, k, seed, recipients_processed, variants_created),
    )
    return int(cur.lastrowid)


def get_next_wave_number(conn: sqlite3.Connection, *, campaign_id: int) -> int:
    """Get the next wave number for this campaign (1-based)."""
    row = conn.execute(
        """
        SELECT MAX(wave_number) AS max_wave
        FROM generation_waves
        WHERE campaign_id = ?
        """,
        (campaign_id,),
    ).fetchone()
    max_wave = row["max_wave"] if row and row["max_wave"] is not None else 0
    return int(max_wave) + 1


def list_generation_waves(conn: sqlite3.Connection, *, campaign_id: int) -> list[sqlite3.Row]:
    """List all generation waves for a campaign, ordered by wave number descending."""
    return list(
        conn.execute(
            """
            SELECT *
            FROM generation_waves
            WHERE campaign_id = ?
            ORDER BY wave_number DESC
            """,
            (campaign_id,),
        ).fetchall()
    )


def get_recipients_with_variants(conn: sqlite3.Connection, *, campaign_id: int) -> set[str]:
    """
    Return set of email addresses that already have variants for this campaign.
    Works for both offline campaigns (via invitation_variants) and online campaigns (via invitations).
    """
    # For offline campaigns, check invitation_variants
    offline_rows = conn.execute(
        """
        SELECT DISTINCT email
        FROM invitation_variants
        WHERE campaign_id = ?
        """,
        (campaign_id,),
    ).fetchall()
    
    # For online campaigns, check invitations
    online_rows = conn.execute(
        """
        SELECT DISTINCT email
        FROM invitations
        WHERE campaign_id = ?
        """,
        (campaign_id,),
    ).fetchall()
    
    result = set()
    for r in offline_rows:
        result.add(str(r["email"]).lower().strip())
    for r in online_rows:
        result.add(str(r["email"]).lower().strip())
    
    return result


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
    """
    Store a respondent submission.

    We store answers_json as an object with a stable top-level key:
      {"answers": {<block_id>: <value>, ...}}

    This matches the Cloudflare Pages API contract and keeps downstream parsing consistent.
    """
    conn.execute(
        """
        INSERT INTO submissions (campaign_id, token, email, answers_json)
        VALUES (?, ?, ?, ?)
        """,
        (campaign_id, token, email, json.dumps({"answers": answers}, ensure_ascii=False)),
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
        ON CONFLICT(campaign_id, token, block_id) DO UPDATE SET
          block_type = excluded.block_type,
          value_text = excluded.value_text,
          value_choice_id = excluded.value_choice_id,
          created_at = CURRENT_TIMESTAMP
        """,
        (campaign_id, token, block_id, block_type, value_text, value_choice_id),
    )


def upsert_submission_from_cloud(
    conn: sqlite3.Connection,
    *,
    campaign_id: int,
    token: str,
    email: str,
    answers_json: str,
    submitted_at: str,
) -> None:
    """
    Idempotent upsert for Cloudflare→local sync.
    - Stores the cloud submission timestamp (submitted_at) verbatim.
    - Overwrites answers_json if re-synced.
    """
    conn.execute(
        """
        INSERT INTO submissions (campaign_id, token, email, answers_json, submitted_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(token) DO UPDATE SET
          campaign_id = excluded.campaign_id,
          email = excluded.email,
          answers_json = excluded.answers_json,
          submitted_at = excluded.submitted_at
        """,
        (campaign_id, token, email, answers_json, submitted_at),
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


def list_recent_submissions(conn: sqlite3.Connection, *, campaign_id: int, limit: int = 25) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT
              s.submitted_at,
              i.email,
              i.token,
              i.opened_at,
              i.questionnaire_hash
            FROM submissions s
            JOIN invitations i ON i.campaign_id = s.campaign_id AND i.token = s.token
            WHERE s.campaign_id = ?
            ORDER BY s.submitted_at DESC
            LIMIT ?
            """,
            (campaign_id, limit),
        ).fetchall()
    )


def list_cloud_recent_submissions(
    conn: sqlite3.Connection,
    *,
    campaign_id: int,
    cloud_base_url: str,
    limit: int = 25,
) -> list[sqlite3.Row]:
    """
    Recent submissions for Cloud mode (tokens are Cloudflare tokens, not local invitation tokens).
    We join submissions.token -> cloud_invitation_tokens.cloud_token to recover email and then
    pull the latest local questionnaire_hash for that email from invitation_variants.
    """
    # Use latest token per email (computed from history).
    return list(
        conn.execute(
            """
            WITH latest_token AS (
              SELECT t.email, t.cloud_token
              FROM cloud_invitation_tokens t
              JOIN (
                SELECT email, MAX(uploaded_at) AS max_uploaded_at
                FROM cloud_invitation_tokens
                WHERE campaign_id = ? AND cloud_base_url = ?
                GROUP BY email
              ) x
                ON x.email = t.email AND x.max_uploaded_at = t.uploaded_at
              WHERE t.campaign_id = ? AND t.cloud_base_url = ?
            )
            SELECT
              s.submitted_at,
              lt.email,
              s.token,
              NULL AS opened_at,
              (
                SELECT questionnaire_hash
                FROM invitation_variants iv
                WHERE iv.campaign_id = ? AND iv.email = lt.email
                ORDER BY iv.created_at DESC
                LIMIT 1
              ) AS questionnaire_hash
            FROM submissions s
            JOIN latest_token lt
              ON lt.cloud_token = s.token
            WHERE s.campaign_id = ?
            ORDER BY s.submitted_at DESC
            LIMIT ?
            """,
            (campaign_id, cloud_base_url, campaign_id, cloud_base_url, campaign_id, campaign_id, limit),
        ).fetchall()
    )


def list_invitation_ledger_rows(conn: sqlite3.Connection, *, campaign_id: int) -> list[sqlite3.Row]:
    """
    One row per invitation with status columns:
    - invited: row exists
    - opened: opened_at is non-null
    - assigned: questionnaire_hash is non-null (snapshot present)
    - submitted: matching submissions row exists
    """
    return list(
        conn.execute(
            """
            SELECT
              i.email,
              i.token,
              i.created_at AS invited_at,
              i.opened_at,
              i.questionnaire_hash,
              s.submitted_at
            FROM invitations i
            LEFT JOIN submissions s
              ON s.campaign_id = i.campaign_id AND s.token = i.token
            WHERE i.campaign_id = ?
            ORDER BY i.email
            """,
            (campaign_id,),
        ).fetchall()
    )


def list_cloud_invitation_ledger_rows(
    conn: sqlite3.Connection,
    *,
    campaign_id: int,
    cloud_base_url: str,
) -> list[sqlite3.Row]:
    """
    One row per email based on latest Cloudflare token (cloud_invitation_tokens) for the campaign/base_url.
    This is used in Cloud mode so 'submitted' reflects submissions keyed by cloud tokens.
    """
    return list(
        conn.execute(
            """
            WITH latest AS (
              SELECT t.email, t.cloud_token, MAX(t.uploaded_at) AS max_uploaded_at
              FROM cloud_invitation_tokens t
              WHERE t.campaign_id = ? AND t.cloud_base_url = ?
              GROUP BY t.email
            )
            SELECT
              l.email,
              l.max_uploaded_at AS invited_at,
              NULL AS opened_at,
              (
                SELECT questionnaire_hash
                FROM invitation_variants iv
                WHERE iv.campaign_id = ? AND iv.email = l.email
                ORDER BY iv.created_at DESC
                LIMIT 1
              ) AS questionnaire_hash,
              s.submitted_at,
              l.cloud_token AS token
            FROM latest l
            LEFT JOIN submissions s
              ON s.campaign_id = ? AND s.token = l.cloud_token
            ORDER BY l.email
            """,
            (campaign_id, cloud_base_url, campaign_id, campaign_id),
        ).fetchall()
    )


def insert_cloud_push(
    conn: sqlite3.Connection,
    *,
    campaign_id: int,
    cloud_base_url: str,
    request_hash: str,
    response_json: str,
) -> int:
    """
    Creates a new push/wave and returns push_id.
    """
    cur = conn.execute(
        """
        INSERT INTO cloud_pushes (campaign_id, cloud_base_url, request_hash, response_json)
        VALUES (?, ?, ?, ?)
        """,
        (campaign_id, cloud_base_url, request_hash, response_json),
    )
    return int(cur.lastrowid)


def insert_cloud_push_tokens(
    conn: sqlite3.Connection,
    *,
    push_id: int,
    campaign_id: int,
    cloud_base_url: str,
    tokens: list[dict[str, str]],
) -> int:
    """
    tokens: list of {email, token}
    Returns number of rows inserted.
    """
    n = 0
    for t in tokens:
        email = str(t.get("email") or "").strip().lower()
        token = str(t.get("token") or "").strip()
        if not email or not token:
            continue
        conn.execute(
            """
            INSERT INTO cloud_invitation_tokens (push_id, campaign_id, cloud_base_url, email, cloud_token)
            VALUES (?, ?, ?, ?, ?)
            """,
            (push_id, campaign_id, cloud_base_url, email, token),
        )
        n += 1
    return n


def list_cloud_pushes(
    conn: sqlite3.Connection,
    *,
    campaign_id: int,
    cloud_base_url: str,
) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT
              p.id AS push_id,
              p.created_at,
              p.request_hash,
              (SELECT COUNT(*) FROM cloud_invitation_tokens t WHERE t.push_id = p.id) AS n_tokens
            FROM cloud_pushes p
            WHERE p.campaign_id = ? AND p.cloud_base_url = ?
            ORDER BY p.created_at DESC
            """,
            (campaign_id, cloud_base_url),
        ).fetchall()
    )


def list_cloud_tokens_for_push(conn: sqlite3.Connection, *, push_id: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT email, cloud_token, uploaded_at
            FROM cloud_invitation_tokens
            WHERE push_id = ?
            ORDER BY email
            """,
            (push_id,),
        ).fetchall()
    )


def list_cloud_latest_tokens(
    conn: sqlite3.Connection,
    *,
    campaign_id: int,
    cloud_base_url: str,
) -> list[sqlite3.Row]:
    """
    Latest token per email (for emailing). Computed from history.
    """
    return list(
        conn.execute(
            """
            SELECT t.email, t.cloud_token, t.uploaded_at, t.push_id
            FROM cloud_invitation_tokens t
            JOIN (
              SELECT email, MAX(uploaded_at) AS max_uploaded_at
              FROM cloud_invitation_tokens
              WHERE campaign_id = ? AND cloud_base_url = ?
              GROUP BY email
            ) x
              ON x.email = t.email AND x.max_uploaded_at = t.uploaded_at
            WHERE t.campaign_id = ? AND t.cloud_base_url = ?
            ORDER BY t.email
            """,
            (campaign_id, cloud_base_url, campaign_id, cloud_base_url),
        ).fetchall()
    )


def get_last_cloud_push(
    conn: sqlite3.Connection,
    *,
    campaign_id: int,
    cloud_base_url: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT created_at, request_hash, id AS push_id
        FROM cloud_pushes
        WHERE campaign_id = ? AND cloud_base_url = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (campaign_id, cloud_base_url),
    ).fetchone()


def record_event(
    conn: sqlite3.Connection,
    *,
    campaign_id: int | None,
    campaign_key: str | None,
    event: str,
    success: bool,
    message: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO event_log (campaign_id, campaign_key, event, status, message)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            campaign_id,
            campaign_key,
            event,
            "success" if success else "failure",
            message,
        ),
    )


def list_events(
    conn: sqlite3.Connection,
    *,
    campaign_id: int | None = None,
    limit: int = 25,
) -> list[sqlite3.Row]:
    q = """
    SELECT campaign_key, event, status, message, created_at
    FROM event_log
    """
    params: list[Any] = []
    if campaign_id is not None:
        q += "WHERE campaign_id = ? "
        params.append(campaign_id)
    q += "ORDER BY created_at DESC LIMIT ? "
    params.append(limit)
    return list(conn.execute(q, tuple(params)).fetchall())


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


def single_select_choice_counts(conn: sqlite3.Connection, *, campaign_id: int) -> list[sqlite3.Row]:
    """
    Aggregate singleSelect answers by (block_id, choice_id).
    """
    return list(
        conn.execute(
            """
            SELECT
              block_id,
              value_choice_id AS choice_id,
              COUNT(*) AS n
            FROM submission_answers
            WHERE campaign_id = ?
              AND block_type = 'singleSelect'
              AND value_choice_id IS NOT NULL
            GROUP BY block_id, value_choice_id
            ORDER BY block_id, n DESC, choice_id
            """,
            (campaign_id,),
        ).fetchall()
    )


def list_free_text_answers(conn: sqlite3.Connection, *, campaign_id: int, limit: int = 500) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT
              created_at,
              token,
              block_id,
              value_text
            FROM submission_answers
            WHERE campaign_id = ?
              AND block_type = 'freeText'
              AND value_text IS NOT NULL
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (campaign_id, limit),
        ).fetchall()
    )


def list_submissions_with_answers(
    conn: sqlite3.Connection,
    *,
    campaign_id: int,
) -> list[sqlite3.Row]:
    """
    List all submissions for a campaign with their full answers.
    Returns email, token, submitted_at, answers_json, and recipient name from strata.
    """
    return list(
        conn.execute(
            """
            SELECT
              s.email,
              s.token,
              s.submitted_at,
              s.answers_json,
              r.strata_json
            FROM submissions s
            LEFT JOIN recipients r ON r.email = s.email
            WHERE s.campaign_id = ?
            ORDER BY s.submitted_at DESC
            """,
            (campaign_id,),
        ).fetchall()
    )


# -----------------------------
# Campaign Recipient Exclusions
# -----------------------------


def list_pending_recipients_for_campaign(
    conn: sqlite3.Connection,
    *,
    campaign_id: int,
) -> list[sqlite3.Row]:
    """
    List recipients who are:
    - Have an invitation for THIS campaign (variants were generated)
    - NOT excluded from this campaign
    - NOT yet submitted for this campaign (no submission exists)
    
    Returns recipient info including first/last name from strata_json.
    Only shows recipients that belong to this campaign (have invitations).
    """
    return list(
        conn.execute(
            """
            SELECT
              r.email,
              r.strata_json,
              i.token,
              i.opened_at,
              i.questionnaire_hash,
              CASE WHEN s.token IS NOT NULL THEN 1 ELSE 0 END AS has_submitted
            FROM invitations i
            INNER JOIN recipients r
              ON r.email = i.email
            LEFT JOIN submissions s
              ON s.campaign_id = i.campaign_id AND s.token = i.token
            WHERE i.campaign_id = ?
              AND i.email NOT IN (
                SELECT email FROM campaign_recipient_exclusions WHERE campaign_id = ?
              )
              AND s.token IS NULL  -- not submitted
            ORDER BY r.email
            """,
            (campaign_id, campaign_id),
        ).fetchall()
    )


def list_excluded_recipients_for_campaign(
    conn: sqlite3.Connection,
    *,
    campaign_id: int,
) -> list[sqlite3.Row]:
    """
    List recipients who are excluded from this campaign.
    """
    return list(
        conn.execute(
            """
            SELECT
              e.email,
              e.excluded_at,
              r.strata_json
            FROM campaign_recipient_exclusions e
            LEFT JOIN recipients r ON r.email = e.email
            WHERE e.campaign_id = ?
            ORDER BY e.email
            """,
            (campaign_id,),
        ).fetchall()
    )


def exclude_recipient_from_campaign(
    conn: sqlite3.Connection,
    *,
    campaign_id: int,
    email: str,
) -> bool:
    """
    Exclude a recipient from a campaign. Also removes any existing invitation for this campaign.
    Returns True if exclusion was added, False if already excluded.
    """
    # Check if already excluded
    existing = conn.execute(
        "SELECT 1 FROM campaign_recipient_exclusions WHERE campaign_id = ? AND email = ?",
        (campaign_id, email),
    ).fetchone()
    if existing:
        return False
    
    # Add exclusion
    conn.execute(
        """
        INSERT INTO campaign_recipient_exclusions (campaign_id, email)
        VALUES (?, ?)
        """,
        (campaign_id, email),
    )
    
    # Remove any existing invitation for this campaign (before it's sent)
    # Only remove if there's no submission yet
    conn.execute(
        """
        DELETE FROM invitations
        WHERE campaign_id = ? AND email = ?
          AND token NOT IN (SELECT token FROM submissions WHERE campaign_id = ?)
        """,
        (campaign_id, email, campaign_id),
    )
    
    # Also remove from invitation_variants
    conn.execute(
        """
        DELETE FROM invitation_variants
        WHERE campaign_id = ? AND email = ?
        """,
        (campaign_id, email),
    )
    
    return True


def restore_recipient_to_campaign(
    conn: sqlite3.Connection,
    *,
    campaign_id: int,
    email: str,
) -> bool:
    """
    Remove exclusion for a recipient from a campaign.
    Returns True if exclusion was removed, False if wasn't excluded.
    """
    cur = conn.execute(
        "DELETE FROM campaign_recipient_exclusions WHERE campaign_id = ? AND email = ?",
        (campaign_id, email),
    )
    return cur.rowcount > 0


def count_pending_recipients(conn: sqlite3.Connection, *, campaign_id: int) -> dict[str, int]:
    """
    Count recipients by status for this campaign.
    Only counts recipients that have invitations for this campaign.
    """
    row = conn.execute(
        """
        SELECT
          -- Total recipients for this campaign (have invitations)
          (SELECT COUNT(*) FROM invitations WHERE campaign_id = ?) AS total_recipients,
          -- Excluded from this campaign
          (SELECT COUNT(*) FROM campaign_recipient_exclusions WHERE campaign_id = ?) AS excluded,
          -- Pending: have invitation, not excluded, not submitted
          (SELECT COUNT(*) 
           FROM invitations i
           LEFT JOIN submissions s ON s.campaign_id = i.campaign_id AND s.token = i.token
           WHERE i.campaign_id = ?
             AND i.email NOT IN (
               SELECT email FROM campaign_recipient_exclusions WHERE campaign_id = ?
             )
             AND s.token IS NULL
          ) AS pending,
          -- Submitted: have invitation and submission
          (SELECT COUNT(*)
           FROM invitations i
           JOIN submissions s ON s.campaign_id = i.campaign_id AND s.token = i.token
           WHERE i.campaign_id = ?
             AND i.email NOT IN (
               SELECT email FROM campaign_recipient_exclusions WHERE campaign_id = ?
             )
          ) AS submitted
        """,
        (campaign_id, campaign_id, campaign_id, campaign_id, campaign_id, campaign_id),
    ).fetchone()
    assert row is not None
    return {
        "total_recipients": int(row["total_recipients"]),
        "excluded": int(row["excluded"]),
        "pending": int(row["pending"]),
        "submitted": int(row["submitted"]),
    }


