"""
scripts/obsidian/ai_session.py - Generate AI Session notes for the Obsidian vault.

For each ai_prompts record on a given date, creates a note file:
    {vault}/AI-Sessions/YYYY-MM-DD-NNN.md  (NNN = 3-digit sequence)

Existing notes are skipped (idempotent).

Usage:
    python scripts/obsidian/ai_session.py [--date YYYY-MM-DD] [--dry-run] [--session-id UUID]
"""

from __future__ import annotations

import argparse
import io
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Windows console UTF-8 (only wrap if not already wrapped and running as __main__)
if sys.platform == "win32" and __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Bootstrap sys.path so this file can be run directly
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from scripts.config import Config  # type: ignore
    _cfg = Config()
except Exception:
    from config import Config  # type: ignore
    _cfg = Config()

try:
    from scripts.obsidian.writer import write_note  # type: ignore
except ImportError:
    from obsidian.writer import write_note  # type: ignore


# ---------------------------------------------------------------------------
# DB query helpers
# ---------------------------------------------------------------------------

def _parse_ts(ts_str: str) -> Optional[datetime]:
    """Parse ISO 8601 timestamp string to an aware datetime."""
    if not ts_str:
        return None
    try:
        ts_str = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(ts_str)
    except (ValueError, AttributeError):
        return None


def _to_local(dt: Optional[datetime]) -> Optional[datetime]:
    """Convert UTC-aware datetime to local timezone."""
    if dt is None:
        return None
    return dt.astimezone()


def _local_date_filter(date_str: str) -> tuple[str, str]:
    """
    Return (utc_start, utc_end) ISO strings that bracket the local day
    identified by date_str (YYYY-MM-DD).

    We compute local midnight in UTC so that the WHERE clause on the UTC
    `timestamp` column correctly captures the local day.
    """
    import time as _time

    # Parse the local date
    local_date = datetime.strptime(date_str, "%Y-%m-%d")

    # local midnight = naive datetime interpreted as local time
    local_midnight = local_date.replace(hour=0, minute=0, second=0, microsecond=0)
    local_next = local_midnight.replace(day=local_midnight.day + 1) \
        if local_midnight.month * 32 + local_midnight.day < local_midnight.month * 32 + 28 \
        else datetime(local_midnight.year, local_midnight.month, local_midnight.day + 1)

    # Convert to UTC using the local timezone offset
    local_tz = datetime.now(timezone.utc).astimezone().tzinfo
    utc_start = local_midnight.replace(tzinfo=local_tz).astimezone(timezone.utc)
    utc_end = local_next.replace(tzinfo=local_tz).astimezone(timezone.utc)

    return (
        utc_start.strftime("%Y-%m-%dT%H:%M:%S"),
        utc_end.strftime("%Y-%m-%dT%H:%M:%S"),
    )


def _query_sessions_for_date(db_path: str, date_str: str) -> list[dict]:
    """
    Query ai_prompts for all records whose local-time date equals date_str.
    Returns list of dicts ordered by timestamp ascending.
    """
    utc_start, utc_end = _local_date_filter(date_str)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, timestamp, tool, project, project_id,
                   prompt_text, response_text,
                   input_tokens, output_tokens, session_id, uuid, cwd
            FROM ai_prompts
            WHERE timestamp >= ? AND timestamp < ?
            ORDER BY timestamp ASC
            """,
            (utc_start, utc_end),
        ).fetchall()
    return [dict(r) for r in rows]


def _query_session_by_id(db_path: str, session_id: str) -> list[dict]:
    """Query all ai_prompts records matching a given session_id (UUID)."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, timestamp, tool, project, project_id,
                   prompt_text, response_text,
                   input_tokens, output_tokens, session_id, uuid, cwd
            FROM ai_prompts
            WHERE session_id = ? OR uuid = ?
            ORDER BY timestamp ASC
            """,
            (session_id, session_id),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Note builder
# ---------------------------------------------------------------------------

def _resolve_tool(row: dict) -> str:
    """Determine the AI tool label for a record."""
    tool = row.get("tool") or ""
    if tool:
        return tool
    # Infer from session_id format or default to claude-code
    return "claude-code"


def _resolve_project(row: dict) -> str:
    """Return a project name string for the record."""
    return (row.get("project") or "").strip() or "unknown"


def build_ai_session_note(row: dict, seq: int, date_str: str) -> str:
    """
    Build the markdown content for an AI Session note.

    Parameters
    ----------
    row:
        A dict from the ai_prompts table.
    seq:
        1-based sequence number for this date.
    date_str:
        The local date string YYYY-MM-DD.

    Returns
    -------
    str
        Full markdown content for the note.
    """
    note_id = f"{date_str}-{seq:03d}"
    tool = _resolve_tool(row)
    project = _resolve_project(row)

    ts = _to_local(_parse_ts(row.get("timestamp", "")))
    time_str = ts.strftime("%H:%M") if ts else ""

    input_tokens = row.get("input_tokens") or 0
    output_tokens = row.get("output_tokens") or 0

    prompt_text = (row.get("prompt_text") or "").strip()
    response_text = (row.get("response_text") or "").strip()

    tags_list = f"[ai-session, {tool}]"

    frontmatter = f"""---
date: {date_str}
time: "{time_str}"
tool: {tool}
project: {project}
tags: {tags_list}
input_tokens: {input_tokens}
output_tokens: {output_tokens}
---"""

    note = f"""{frontmatter}

# AI 세션 {note_id}

## 프롬프트

{prompt_text if prompt_text else "(없음)"}

## 결과

{response_text if response_text else "(없음)"}

## 생성된 파일

"""

    return note


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def generate_ai_sessions(
    date_str: str,
    db_path: str,
    vault_path: str,
    dry_run: bool = False,
    session_id: Optional[str] = None,
) -> list[str]:
    """
    Generate AI Session note files for a given date (or specific session_id).

    Returns a list of relative paths to files that were written (or would be
    written in dry-run mode).
    """
    if session_id:
        rows = _query_session_by_id(db_path, session_id)
        if not rows:
            print(f"[ai_session] No records found for session_id/uuid: {session_id}")
            return []
        # Determine date from first row
        ts = _to_local(_parse_ts(rows[0].get("timestamp", "")))
        if ts:
            date_str = ts.strftime("%Y-%m-%d")
    else:
        rows = _query_sessions_for_date(db_path, date_str)

    if not rows:
        print(f"[ai_session] No AI prompt records found for date: {date_str}")
        return []

    print(f"[ai_session] Found {len(rows)} record(s) for {date_str}")

    written_paths: list[str] = []

    for seq, row in enumerate(rows, start=1):
        note_id = f"{date_str}-{seq:03d}"
        relative_path = f"AI-Sessions/{note_id}.md"
        content = build_ai_session_note(row, seq, date_str)

        if dry_run:
            print(f"\n{'='*60}")
            print(f"[DRY-RUN] Would write: {relative_path}")
            print(f"{'='*60}")
            print(content[:500] + ("..." if len(content) > 500 else ""))
            written_paths.append(relative_path)
        else:
            written = write_note(vault_path, relative_path, content, overwrite=False)
            if written:
                print(f"[ai_session] Created: {relative_path}")
                written_paths.append(relative_path)
            else:
                print(f"[ai_session] Skipped (exists): {relative_path}")

    return written_paths


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate AI Session notes in the Obsidian vault."
    )
    parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        default=None,
        help="Date to generate notes for (default: today in local time).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print note content to stdout without writing files.",
    )
    parser.add_argument(
        "--session-id",
        metavar="UUID",
        default=None,
        help="Generate note for a specific session_id or uuid.",
    )
    args = parser.parse_args()

    # Resolve date
    if args.date:
        date_str = args.date
    else:
        date_str = datetime.now().strftime("%Y-%m-%d")

    try:
        vault_path = _cfg.get_vault_path()
    except RuntimeError as exc:
        print(f"[ai_session] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    db_path = _cfg.get_db_path()

    if not Path(db_path).exists():
        print(
            f"[ai_session] ERROR: Database not found at {db_path}. "
            "Run: python scripts/init_db.py",
            file=sys.stderr,
        )
        sys.exit(1)

    generate_ai_sessions(
        date_str=date_str,
        db_path=db_path,
        vault_path=vault_path,
        dry_run=args.dry_run,
        session_id=args.session_id,
    )


if __name__ == "__main__":
    main()
