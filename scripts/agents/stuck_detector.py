"""
scripts/agents/stuck_detector.py - DayTracker Phase 6 Stuck Detector Agent.

Detects when the user appears to be stuck on a file (same file modified 3+
times in a short window without a git commit) and suggests hints based on
past AI sessions that dealt with similar files or directories.

CLI:
    python scripts/agents/stuck_detector.py [--dry-run] [--threshold-minutes 30]

Triggered by watcher_daemon.py every 15 minutes (LIVE mode only).

Output (when stuck detected):
    [DayTracker] 혹시 막히셨나요?
      파일: scripts/watcher_daemon.py (최근 30분간 7번 수정)

      비슷한 상황의 과거 세션:
      - 2026-02-21 23:44  "watcher_daemon 스레드 종료 문제..."
        -> "threading.Event를 사용해 graceful shutdown 구현"
"""

from __future__ import annotations

import argparse
import io
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Windows console UTF-8
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    if hasattr(sys.stdout, "buffer") and not getattr(sys.stdout, "_daytracker_wrapped", False):
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
        )
        sys.stdout._daytracker_wrapped = True  # type: ignore[attr-defined]
    if hasattr(sys.stderr, "buffer") and not getattr(sys.stderr, "_daytracker_wrapped", False):
        sys.stderr = io.TextIOWrapper(
            sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True
        )
        sys.stderr._daytracker_wrapped = True  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Bootstrap sys.path
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from scripts.config import Config  # type: ignore
    _cfg = Config()
except Exception:
    try:
        from config import Config  # type: ignore
        _cfg = Config()
    except Exception:
        _cfg = None  # type: ignore


# ---------------------------------------------------------------------------
# Core detection functions
# ---------------------------------------------------------------------------

def detect_stuck_files(
    db_path: str,
    threshold_minutes: int = 30,
    min_modify_count: int = 3,
) -> list[dict]:
    """
    Return files where the same file has been modified 3+ times
    in the last threshold_minutes without a git commit in between.

    Parameters
    ----------
    db_path:
        Path to worklog.db
    threshold_minutes:
        Look-back window in minutes.
    min_modify_count:
        Minimum number of modify events to flag as "stuck".

    Returns
    -------
    list[dict]:
        [{'file_path': ..., 'modify_count': N,
          'first_seen': ISO str, 'last_seen': ISO str}]
    """
    db = Path(db_path)
    if not db.exists():
        return []

    # Calculate the UTC cutoff time
    cutoff_utc = (
        datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)
    ).strftime("%Y-%m-%dT%H:%M:%S")

    try:
        with sqlite3.connect(str(db), timeout=5) as conn:
            conn.row_factory = sqlite3.Row

            # Find files with 3+ modify events in the window
            rows = conn.execute(
                """
                SELECT
                    file_path,
                    COUNT(*) AS modify_count,
                    MIN(timestamp) AS first_seen,
                    MAX(timestamp) AS last_seen
                FROM file_events
                WHERE event_type IN ('modified', 'created')
                  AND timestamp >= ?
                  AND file_path IS NOT NULL
                GROUP BY file_path
                HAVING COUNT(*) >= ?
                ORDER BY COUNT(*) DESC
                """,
                (cutoff_utc, min_modify_count),
            ).fetchall()

        if not rows:
            return []

        # Filter out files that had a git commit between first_seen and last_seen
        # (if a commit happened, user probably wasn't stuck - they made progress)
        stuck: list[dict] = []
        for row in rows:
            file_path = row["file_path"]
            first_seen = row["first_seen"]
            last_seen = row["last_seen"]
            has_commit = _has_commit_in_range(db_path, first_seen, last_seen)
            if not has_commit:
                stuck.append({
                    "file_path": file_path,
                    "modify_count": row["modify_count"],
                    "first_seen": first_seen,
                    "last_seen": last_seen,
                })

        return stuck

    except sqlite3.Error as exc:
        print(f"[stuck_detector] DB error: {exc}", file=sys.stderr)
        return []


def _has_commit_in_range(db_path: str, start_ts: str, end_ts: str) -> bool:
    """Return True if there is a git commit recorded between start_ts and end_ts."""
    try:
        with sqlite3.connect(db_path, timeout=5) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) FROM activity_log
                WHERE event_type = 'git_commit'
                  AND timestamp >= ?
                  AND timestamp <= ?
                """,
                (start_ts, end_ts),
            ).fetchone()
        return bool(row and row[0] > 0)
    except sqlite3.Error:
        return False


def find_similar_past_sessions(
    db_path: str,
    file_path: str,
    limit: int = 3,
) -> list[dict]:
    """
    Search ai_prompts for sessions where prompt_text or response_text contains
    the filename or its parent directory name.

    Parameters
    ----------
    db_path:
        Path to worklog.db.
    file_path:
        The file path that triggered the stuck detection.
    limit:
        Maximum number of past sessions to return.

    Returns
    -------
    list[dict]:
        [{'timestamp': ..., 'project': ...,
          'prompt_text': first 100 chars,
          'response_text': first 100 chars}]
    """
    db = Path(db_path)
    if not db.exists():
        return []

    p = Path(file_path)
    # Use the filename stem and parent directory name as keywords
    keywords = []
    if p.name:
        keywords.append(p.name)       # e.g. "watcher_daemon.py"
        keywords.append(p.stem)        # e.g. "watcher_daemon"
    if p.parent and p.parent.name:
        keywords.append(p.parent.name) # e.g. "scripts"

    # Deduplicate, keep order
    seen: set[str] = set()
    unique_keywords: list[str] = []
    for k in keywords:
        if k and k not in seen:
            seen.add(k)
            unique_keywords.append(k)

    if not unique_keywords:
        return []

    try:
        with sqlite3.connect(str(db), timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            results: list[dict] = []
            seen_ids: set[int] = set()

            for keyword in unique_keywords:
                if len(results) >= limit:
                    break
                rows = conn.execute(
                    """
                    SELECT id, timestamp, tool,
                           prompt_text, response_text,
                           project_id
                    FROM ai_prompts
                    WHERE (prompt_text LIKE ? OR response_text LIKE ?)
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (f"%{keyword}%", f"%{keyword}%", limit * 2),
                ).fetchall()

                for row in rows:
                    if row["id"] in seen_ids:
                        continue
                    seen_ids.add(row["id"])

                    # Resolve project name
                    project_name = _resolve_project(conn, row["project_id"])

                    # Trim texts for display
                    prompt_preview = _preview(row["prompt_text"], 100)
                    response_preview = _preview(row["response_text"], 100)

                    results.append({
                        "timestamp": row["timestamp"],
                        "project": project_name,
                        "prompt_text": prompt_preview,
                        "response_text": response_preview,
                    })
                    if len(results) >= limit:
                        break

        return results[:limit]

    except sqlite3.Error as exc:
        print(f"[stuck_detector] DB error in find_similar_past_sessions: {exc}", file=sys.stderr)
        return []


def _resolve_project(conn: sqlite3.Connection, project_id: Optional[int]) -> str:
    """Look up project name by id."""
    if project_id is None:
        return "unknown"
    try:
        row = conn.execute(
            "SELECT name FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        return row[0] if row else "unknown"
    except sqlite3.Error:
        return "unknown"


def _preview(text: Optional[str], max_len: int) -> str:
    """Return a trimmed single-line preview of text."""
    if not text:
        return ""
    # Collapse whitespace/newlines to single spaces
    clean = " ".join(text.split())
    if len(clean) > max_len:
        return clean[:max_len] + "..."
    return clean


# ---------------------------------------------------------------------------
# Hint generation
# ---------------------------------------------------------------------------

def generate_hint(stuck_file: dict, past_sessions: list[dict]) -> str:
    """
    Generate a helpful hint message for a stuck file.

    Parameters
    ----------
    stuck_file:
        Dict with keys: file_path, modify_count, first_seen, last_seen.
    past_sessions:
        List of similar past AI sessions.

    Returns
    -------
    str:
        A formatted hint string to print to stdout.
    """
    file_path = stuck_file["file_path"]
    count = stuck_file["modify_count"]
    first_seen = stuck_file["first_seen"]
    last_seen = stuck_file["last_seen"]

    # Calculate minutes elapsed
    try:
        ts_start = _parse_ts(first_seen)
        ts_end = _parse_ts(last_seen)
        if ts_start and ts_end:
            elapsed_minutes = max(1, int((ts_end - ts_start).total_seconds() / 60))
        else:
            elapsed_minutes = 0
    except Exception:
        elapsed_minutes = 0

    duration_str = f"최근 {elapsed_minutes}분간" if elapsed_minutes > 0 else "최근"

    lines = [
        f"\n[DayTracker] 혹시 막히셨나요?",
        f"  파일: {file_path} ({duration_str} {count}번 수정)",
    ]

    if past_sessions:
        lines.append("")
        lines.append("  비슷한 상황의 과거 세션:")
        for session in past_sessions:
            ts = session.get("timestamp", "")
            ts_local = _ts_to_local_str(ts)
            project = session.get("project", "unknown")
            prompt = session.get("prompt_text", "")
            response = session.get("response_text", "")

            lines.append(f'  - {ts_local} [{project}]  "{prompt}"')
            if response:
                lines.append(f'    -> "{response}"')
    else:
        lines.append("")
        lines.append("  (과거 유사 세션을 찾지 못했습니다.)")
        lines.append("  Claude Code에 질문해 보세요!")

    return "\n".join(lines)


def _parse_ts(ts_str: str) -> Optional[datetime]:
    """Parse an ISO timestamp string into a datetime (UTC-aware)."""
    if not ts_str:
        return None
    try:
        ts_str = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(ts_str)
    except (ValueError, AttributeError):
        try:
            # Try naive datetime and treat as UTC
            return datetime.strptime(ts_str[:19], "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return None


def _ts_to_local_str(ts_str: str) -> str:
    """Convert UTC ISO timestamp to local date-time string."""
    dt = _parse_ts(ts_str)
    if dt is None:
        return ts_str
    local_dt = dt.astimezone()
    return local_dt.strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# Briefing note writer
# ---------------------------------------------------------------------------

def write_briefing_note(
    vault_path: str,
    date_str: str,
    hints: list[str],
) -> Optional[str]:
    """
    Append hint messages to {vault}/Briefings/YYYY-MM-DD-hints.md.

    Creates the file if it does not exist.
    Returns the path written, or None on failure.
    """
    try:
        briefings_dir = Path(vault_path) / "Briefings"
        briefings_dir.mkdir(parents=True, exist_ok=True)
        note_path = briefings_dir / f"{date_str}-hints.md"

        timestamp = datetime.now().strftime("%H:%M:%S")
        header = f"\n## {timestamp} 힌트\n\n"
        body = "\n".join(f"> {line}" if line.startswith("[DayTracker]") else line for line in hints)

        # Append to existing or create new
        mode = "a" if note_path.exists() else "w"
        if mode == "w":
            # Write file header on first creation
            frontmatter = (
                f"---\n"
                f"date: {date_str}\n"
                f"tags: [briefing, hints]\n"
                f"---\n\n"
                f"# {date_str} 힌트 브리핑\n"
            )
            with open(note_path, "w", encoding="utf-8") as fh:
                fh.write(frontmatter + header + body + "\n")
        else:
            with open(note_path, "a", encoding="utf-8") as fh:
                fh.write(header + body + "\n")

        return str(note_path)
    except Exception as exc:
        print(f"[stuck_detector] Error writing briefing note: {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Main run function
# ---------------------------------------------------------------------------

def run(
    threshold_minutes: int = 30,
    dry_run: bool = False,
    config=None,
    write_note: bool = True,
) -> list[dict]:
    """
    Run the stuck detector.

    Parameters
    ----------
    threshold_minutes:
        Look-back window for detecting repeated edits.
    dry_run:
        If True, do not write to any files.
    config:
        Optional Config instance (will load from config.yaml if None).
    write_note:
        If True (and not dry_run), write hints to the vault Briefings note.

    Returns
    -------
    list[dict]:
        List of stuck files detected.
    """
    if config is None:
        config = _cfg

    if config is None:
        print("[stuck_detector] ERROR: No config available.", file=sys.stderr)
        return []

    try:
        db_path = config.get_db_path()
    except Exception as exc:
        print(f"[stuck_detector] ERROR getting db_path: {exc}", file=sys.stderr)
        return []

    if not Path(db_path).exists():
        print(
            f"[stuck_detector] DB not found at {db_path}. "
            "Run: python scripts/init_db.py",
            file=sys.stderr,
        )
        return []

    stuck_files = detect_stuck_files(db_path, threshold_minutes=threshold_minutes)

    if not stuck_files:
        print(
            f"[stuck_detector] No stuck patterns detected "
            f"(threshold: {threshold_minutes} min, min edits: 3)."
        )
        return []

    hint_lines: list[str] = []
    for stuck_file in stuck_files:
        past = find_similar_past_sessions(db_path, stuck_file["file_path"])
        hint = generate_hint(stuck_file, past)
        print(hint)
        hint_lines.append(hint)

    # Optionally write to vault
    if write_note and not dry_run:
        try:
            vault_path = config.get_vault_path()
            date_str = datetime.now().strftime("%Y-%m-%d")
            note_path = write_briefing_note(vault_path, date_str, hint_lines)
            if note_path:
                print(f"[stuck_detector] Hint written to: {note_path}")
        except Exception as exc:
            print(f"[stuck_detector] Could not write briefing note: {exc}", file=sys.stderr)

    return stuck_files


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "DayTracker Stuck Detector Agent.\n"
            "Detects repeated file edits and suggests hints from past AI sessions."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print output only; do not write to vault.",
    )
    parser.add_argument(
        "--threshold-minutes",
        type=int,
        default=30,
        metavar="N",
        help="Look-back window in minutes (default: 30).",
    )
    parser.add_argument(
        "--no-note",
        action="store_true",
        help="Do not write hints to the vault Briefings note.",
    )
    args = parser.parse_args()

    run(
        threshold_minutes=args.threshold_minutes,
        dry_run=args.dry_run,
        write_note=not args.no_note,
    )


if __name__ == "__main__":
    main()
