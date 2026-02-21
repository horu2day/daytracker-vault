"""
scripts/init_db.py - Initialize the DayTracker SQLite database.

Creates data/worklog.db with all tables and indexes.
Idempotent: safe to run multiple times (uses IF NOT EXISTS).

Usage:
    python scripts/init_db.py
    python -m scripts.init_db
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Schema definitions
# ---------------------------------------------------------------------------

TABLES: list[str] = [
    # projects: one row per tracked project folder
    """
    CREATE TABLE IF NOT EXISTS projects (
        id          INTEGER PRIMARY KEY,
        name        TEXT    NOT NULL UNIQUE,
        path        TEXT,
        status      TEXT    DEFAULT 'active',
        created_at  TEXT
    )
    """,

    # activity_log: high-level events (window focus, file save, git commit …)
    """
    CREATE TABLE IF NOT EXISTS activity_log (
        id           INTEGER PRIMARY KEY,
        timestamp    TEXT    NOT NULL,
        duration_s   INTEGER,
        event_type   TEXT    NOT NULL,
        project_id   INTEGER REFERENCES projects(id),
        app_name     TEXT,
        summary      TEXT,
        data         TEXT
    )
    """,

    # ai_prompts: one row per AI interaction
    """
    CREATE TABLE IF NOT EXISTS ai_prompts (
        id            INTEGER PRIMARY KEY,
        activity_id   INTEGER REFERENCES activity_log(id),
        timestamp     TEXT    NOT NULL,
        tool          TEXT,
        project_id    INTEGER REFERENCES projects(id),
        prompt_text   TEXT,
        response_text TEXT,
        input_tokens  INTEGER,
        output_tokens INTEGER,
        session_id    TEXT
    )
    """,

    # file_events: one row per file create/modify/delete event
    """
    CREATE TABLE IF NOT EXISTS file_events (
        id           INTEGER PRIMARY KEY,
        activity_id  INTEGER REFERENCES activity_log(id),
        timestamp    TEXT    NOT NULL,
        file_path    TEXT,
        event_type   TEXT,
        project_id   INTEGER REFERENCES projects(id),
        file_size    INTEGER
    )
    """,
]

INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_activity_timestamp ON activity_log(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_activity_project   ON activity_log(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_file_path          ON file_events(file_path)",
    "CREATE INDEX IF NOT EXISTS idx_ai_session         ON ai_prompts(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_ai_project         ON ai_prompts(project_id)",
]


# ---------------------------------------------------------------------------
# Main initializer
# ---------------------------------------------------------------------------

def init_db(db_path: str | Path | None = None) -> str:
    """
    Create the database and all tables/indexes.

    Parameters
    ----------
    db_path:
        Absolute path to the .db file.  If None, uses data/worklog.db
        relative to the project root (two levels up from this file).

    Returns
    -------
    str
        The path of the database file that was initialised.
    """
    if db_path is None:
        project_root = Path(__file__).resolve().parent.parent
        db_path = project_root / "data" / "worklog.db"
    else:
        db_path = Path(db_path)

    # Ensure parent directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[init_db] Initialising database at: {db_path}")

    created_tables: list[str] = []
    created_indexes: list[str] = []
    skipped_tables: list[str] = []
    skipped_indexes: list[str] = []

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

        # --- Tables ---
        for ddl in TABLES:
            # Extract table name for reporting
            table_name = _extract_name(ddl, "TABLE")
            try:
                conn.execute(ddl)
                # Check if table already existed before this run
                row = conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type='table' AND name=?",
                    (table_name,),
                ).fetchone()
                if row and row[0]:
                    # We can't distinguish "just created" vs "already existed"
                    # with IF NOT EXISTS, so we track it simply.
                    created_tables.append(table_name)
            except sqlite3.Error as exc:
                print(
                    f"[init_db] ERROR creating table {table_name}: {exc}",
                    file=sys.stderr,
                )

        # --- Indexes ---
        for ddl in INDEXES:
            idx_name = _extract_index_name(ddl)
            try:
                conn.execute(ddl)
                created_indexes.append(idx_name)
            except sqlite3.Error as exc:
                print(
                    f"[init_db] ERROR creating index {idx_name}: {exc}",
                    file=sys.stderr,
                )

        conn.commit()

    # --- Summary ---
    print(f"\n[init_db] Database ready: {db_path}")
    print(f"  Tables  : {', '.join(created_tables) if created_tables else '(none)'}")
    print(f"  Indexes : {', '.join(created_indexes) if created_indexes else '(none)'}")
    print(
        "\n  Tables present: "
        + _list_tables(str(db_path))
    )

    return str(db_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_name(ddl: str, kind: str) -> str:
    """Extract table name from a CREATE TABLE IF NOT EXISTS ... statement."""
    tokens = ddl.split()
    try:
        idx = tokens.index(kind)
        # Skip IF NOT EXISTS if present
        if tokens[idx + 1].upper() == "IF":
            return tokens[idx + 4]
        return tokens[idx + 1]
    except (ValueError, IndexError):
        return "<unknown>"


def _extract_index_name(ddl: str) -> str:
    """Extract index name from a CREATE INDEX IF NOT EXISTS ... statement."""
    tokens = ddl.split()
    try:
        idx = tokens.index("INDEX")
        if tokens[idx + 1].upper() == "IF":
            return tokens[idx + 4]
        return tokens[idx + 1]
    except (ValueError, IndexError):
        return "<unknown>"


def _list_tables(db_path: str) -> str:
    """Return a comma-separated list of tables in the database."""
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        return ", ".join(r[0] for r in rows) if rows else "(none)"
    except Exception:  # noqa: BLE001
        return "(error reading tables)"


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Initialise the DayTracker SQLite database (idempotent)."
    )
    parser.add_argument(
        "--db-path",
        help="Path to the SQLite database file (default: data/worklog.db)",
        default=None,
    )
    args = parser.parse_args()

    try:
        path = init_db(args.db_path)
        print(f"\n[init_db] Done. Database: {path}")
    except Exception as exc:  # noqa: BLE001
        print(f"[init_db] FATAL: {exc}", file=sys.stderr)
        sys.exit(1)
