"""
Claude Code conversation history collector for DayTracker.

Parses JSONL files from ~/.claude/projects/ and upserts conversation
sessions into the ai_prompts table in worklog.db.

Usage:
    python scripts/collectors/claude_code.py [--dry-run] [--date YYYY-MM-DD]
"""

import argparse
import io
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Windows 콘솔 UTF-8 출력 보장 (guard against double-wrapping when imported)
if sys.platform == "win32":
    if hasattr(sys.stdout, "buffer") and not getattr(sys.stdout, "_daytracker_wrapped", False):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stdout._daytracker_wrapped = True  # type: ignore[attr-defined]
    if hasattr(sys.stderr, "buffer") and not getattr(sys.stderr, "_daytracker_wrapped", False):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
        sys.stderr._daytracker_wrapped = True  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Bootstrap: make sure the project root and scripts/ dir are importable
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
_SCRIPTS_DIR = _SCRIPT_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# ---------------------------------------------------------------------------
# Config: try to use the Config class from scripts/config.py.
# Fall back gracefully to sensible defaults if anything goes wrong.
# ---------------------------------------------------------------------------
try:
    from config import Config  # type: ignore
    _cfg = Config()
    CLAUDE_HISTORY_PATH = _cfg.get_claude_history_path()
    DB_PATH = _cfg.get_db_path()
    _extra_sensitive_patterns = _cfg.sensitive_patterns
except Exception:
    CLAUDE_HISTORY_PATH = os.path.expanduser("~/.claude/projects")
    DB_PATH = str(_PROJECT_ROOT / "data" / "worklog.db")
    _extra_sensitive_patterns = []

# Sensitive filter (applied before saving to DB)
try:
    from processors.sensitive_filter import SensitiveFilter as _SF  # type: ignore
    _sensitive_filter = _SF(extra_patterns=_extra_sensitive_patterns)
except Exception:
    _sensitive_filter = None  # type: ignore[assignment]


def _mask(text: str) -> str:
    """Mask sensitive data in *text* using SensitiveFilter (or identity if unavailable)."""
    if not text:
        return text
    if _sensitive_filter is not None:
        masked, found = _sensitive_filter.mask(text)
        if found:
            log.debug("Masked sensitive patterns: %s", ", ".join(found))
        return masked
    return text

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_TEXT_LEN = 10_000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_text(content) -> str:
    """
    Extract plain text from a message content field.

    content may be:
      - a plain string
      - a list of content blocks (each may have type/text fields)
    """
    if isinstance(content, str):
        return content[:MAX_TEXT_LEN]

    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)[:MAX_TEXT_LEN]

    return ""


def _cwd_to_project(cwd: str) -> str:
    """Convert a Windows/Unix cwd path to a human-readable project name."""
    # Normalise backslashes
    normalised = cwd.replace("\\", "/")
    return Path(normalised).name or normalised


def _parse_timestamp(ts_str: str) -> Optional[datetime]:
    """Parse an ISO 8601 timestamp string into a timezone-aware datetime."""
    try:
        # Python 3.11+ handles 'Z' natively; for 3.7-3.10 replace it
        ts_str = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(ts_str)
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Core parsing
# ---------------------------------------------------------------------------

def parse_jsonl_file(filepath: str) -> list:
    """
    Read a JSONL file and return a list of parsed entry dicts.

    Malformed lines are silently skipped (logged at WARNING level).
    Returns an empty list if the file cannot be read.
    """
    entries = []
    try:
        with open(filepath, encoding="utf-8") as fh:
            for lineno, raw_line in enumerate(fh, start=1):
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    entry = json.loads(raw_line)
                    entries.append(entry)
                except json.JSONDecodeError as exc:
                    log.warning("Skipping malformed JSON at %s line %d: %s", filepath, lineno, exc)
    except OSError as exc:
        log.warning("Cannot read file %s: %s", filepath, exc)
    return entries


def extract_sessions(entries: list) -> list:
    """
    Group raw JSONL entries into paired (user prompt + assistant response) records.

    Returns a list of dicts with keys:
        session_id, project, cwd, timestamp, uuid,
        prompt_text, response_text
    """
    # Index all entries by uuid for fast lookup
    by_uuid: dict = {}
    for entry in entries:
        uid = entry.get("uuid")
        if uid:
            by_uuid[uid] = entry

    # Build a mapping: user_uuid -> assistant entry (parentUuid == user_uuid)
    assistant_by_parent: dict = {}
    for entry in entries:
        if entry.get("type") == "assistant":
            parent = entry.get("parentUuid")
            if parent and parent not in assistant_by_parent:
                assistant_by_parent[parent] = entry

    sessions = []
    for entry in entries:
        if entry.get("type") != "user":
            continue

        # Skip tool-result / internal messages (userType != "external") if present
        # (Claude Code marks real human messages as "external")
        # We keep all user messages to be safe; filter downstream if needed.

        uid = entry.get("uuid", "")
        session_id = entry.get("sessionId", "")
        cwd = entry.get("cwd", "")
        ts_raw = entry.get("timestamp", "")
        content = entry.get("message", {}).get("content", "")

        prompt_text = _extract_text(content)
        if not prompt_text.strip():
            # Skip empty/tool-only user turns
            continue

        # Find paired assistant message
        asst_entry = assistant_by_parent.get(uid)
        if asst_entry:
            asst_content = asst_entry.get("message", {}).get("content", "")
            response_text = _extract_text(asst_content)
        else:
            response_text = ""

        sessions.append(
            {
                "session_id": session_id,
                "project": _cwd_to_project(cwd),
                "cwd": cwd,
                "timestamp": ts_raw,
                "uuid": uid,
                "prompt_text": prompt_text,
                "response_text": response_text,
            }
        )

    return sessions


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ai_prompts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    uuid        TEXT NOT NULL,
    project     TEXT,
    cwd         TEXT,
    timestamp   TEXT,
    prompt_text TEXT,
    response_text TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(session_id, uuid)
);
"""

_UPSERT_SQL = """
INSERT OR IGNORE INTO ai_prompts
    (session_id, uuid, project, cwd, timestamp, prompt_text, response_text)
VALUES
    (:session_id, :uuid, :project, :cwd, :timestamp, :prompt_text, :response_text)
"""


def sync_to_db(sessions: list, db_path: str) -> int:
    """
    Upsert session records into the ai_prompts table.

    Returns the count of newly inserted records.
    """
    if not sessions:
        return 0

    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error as exc:
        log.error("Cannot open database %s: %s", db_path, exc)
        return 0

    inserted = 0
    try:
        with conn:
            conn.execute(_CREATE_TABLE_SQL)
            for record in sessions:
                # Mask sensitive data before persisting
                sanitised = dict(record)
                sanitised["prompt_text"] = _mask(record.get("prompt_text", ""))
                sanitised["response_text"] = _mask(record.get("response_text", ""))
                cur = conn.execute(_UPSERT_SQL, sanitised)
                inserted += cur.rowcount
    except sqlite3.Error as exc:
        log.error("Database error: %s", exc)
    finally:
        conn.close()

    return inserted


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _find_jsonl_files(base_path: str) -> list:
    """Return all .jsonl files found recursively under base_path."""
    results = []
    try:
        base = Path(base_path)
        if not base.exists():
            log.warning("Claude history path does not exist: %s", base_path)
            return results
        for jsonl_file in base.rglob("*.jsonl"):
            results.append(str(jsonl_file))
    except OSError as exc:
        log.warning("Error scanning %s: %s", base_path, exc)
    return results


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(dry_run: bool = False, date_filter: Optional[str] = None) -> None:
    """
    Discover all JSONL files, parse them, optionally filter by date,
    and upsert to the database.

    Args:
        dry_run:     If True, print parsed sessions instead of writing to DB.
        date_filter: Optional date string 'YYYY-MM-DD' to restrict sessions.
    """
    log.info("Claude history path: %s", CLAUDE_HISTORY_PATH)
    log.info("Database path:       %s", DB_PATH)
    if dry_run:
        log.info("DRY-RUN mode — no database writes")

    jsonl_files = _find_jsonl_files(CLAUDE_HISTORY_PATH)
    log.info("Found %d JSONL file(s)", len(jsonl_files))

    all_sessions = []
    for filepath in jsonl_files:
        entries = parse_jsonl_file(filepath)
        sessions = extract_sessions(entries)
        all_sessions.extend(sessions)

    log.info("Parsed %d user turn(s) across all files", len(all_sessions))

    # Apply date filter
    if date_filter:
        filtered = []
        for s in all_sessions:
            ts = _parse_timestamp(s.get("timestamp", ""))
            if ts and ts.strftime("%Y-%m-%d") == date_filter:
                filtered.append(s)
        log.info(
            "After date filter '%s': %d session(s) remaining",
            date_filter,
            len(filtered),
        )
        all_sessions = filtered

    if not all_sessions:
        log.info("No sessions to process.")
        return

    if dry_run:
        _print_sessions(all_sessions)
    else:
        new_count = sync_to_db(all_sessions, DB_PATH)
        log.info("Inserted %d new record(s) into ai_prompts", new_count)


def _print_sessions(sessions: list) -> None:
    """Pretty-print sessions for dry-run output."""
    print(f"\n{'='*70}")
    print(f"DRY-RUN: {len(sessions)} session(s) found")
    print(f"{'='*70}\n")
    for i, s in enumerate(sessions, start=1):
        ts = _parse_timestamp(s["timestamp"])
        ts_str = ts.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z") if ts else s["timestamp"]
        print(f"[{i:03d}] {ts_str}")
        print(f"      Project  : {s['project']}")
        print(f"      Session  : {s['session_id']}")
        print(f"      UUID     : {s['uuid']}")
        prompt_preview = s["prompt_text"].replace("\n", " ")[:120]
        response_preview = s["response_text"].replace("\n", " ")[:120]
        print(f"      Prompt   : {prompt_preview}")
        print(f"      Response : {response_preview}")
        print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect Claude Code conversation history into worklog.db"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print parsed sessions without writing to the database",
    )
    parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        default=None,
        help="Only process sessions from this date (UTC)",
    )
    return parser


if __name__ == "__main__":
    parser = _build_arg_parser()
    args = parser.parse_args()
    run(dry_run=args.dry_run, date_filter=args.date)
