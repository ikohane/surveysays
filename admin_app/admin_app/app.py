from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from flask import Flask

from .db import Db
from .routes.all_routes import register as register_all_routes


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

    # Register all routes on this app (keeps endpoint names stable without touching templates).
    register_all_routes(app, db)

    return app


def _main() -> None:
    app = create_app()
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5055")), debug=True)


if __name__ == "__main__":
    _main()


