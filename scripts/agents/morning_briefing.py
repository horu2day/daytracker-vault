"""
scripts/agents/morning_briefing.py - DayTracker Morning Briefing Agent.

Generates a morning briefing showing:
  1. Yesterday's projects worked on (AI sessions + file events counts)
  2. Incomplete TODOs from yesterday's Daily Note
  3. Recommended start task (last modified file, most recent project)
  4. Whether today has any activity records yet

Writes the briefing to:
    {vault}/Briefings/YYYY-MM-DD-morning.md

And prints it to stdout with Unicode box-drawing decorations.

Usage:
    python scripts/agents/morning_briefing.py [--dry-run]
    python -m scripts.agents.morning_briefing [--dry-run]
"""

from __future__ import annotations

import argparse
import io
import re
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Windows console UTF-8 (guard against double-wrapping)
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
# Timestamp helpers (shared pattern from daily_note.py)
# ---------------------------------------------------------------------------

def _parse_ts(ts_str: str) -> Optional[datetime]:
    if not ts_str:
        return None
    try:
        ts_str = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(ts_str)
    except (ValueError, AttributeError):
        return None


def _to_local(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.astimezone()


def _local_day_utc_bounds(date_str: str) -> tuple[str, str]:
    """Return (utc_start, utc_end) strings bracketing the local calendar day."""
    local_date = datetime.strptime(date_str, "%Y-%m-%d")
    local_tz = datetime.now(timezone.utc).astimezone().tzinfo
    local_midnight = local_date.replace(tzinfo=local_tz)
    local_next = local_midnight + timedelta(days=1)
    utc_start = local_midnight.astimezone(timezone.utc)
    utc_end = local_next.astimezone(timezone.utc)
    return (
        utc_start.strftime("%Y-%m-%dT%H:%M:%S"),
        utc_end.strftime("%Y-%m-%dT%H:%M:%S"),
    )


# ---------------------------------------------------------------------------
# Core data-gathering functions
# ---------------------------------------------------------------------------

def get_yesterday_summary(db_path: str, vault_path: str) -> dict:
    """
    Query yesterday's activity from the database.

    Returns a dict with keys:
        date_str      - yesterday as YYYY-MM-DD
        projects      - list of dicts {name, ai_count, file_count, last_ts}
        total_ai      - total AI sessions yesterday
        total_files   - total file events yesterday
        earliest_today - earliest activity_log timestamp for today (or None)
    """
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    today_str = date.today().strftime("%Y-%m-%d")

    result: dict = {
        "date_str": yesterday,
        "projects": [],
        "total_ai": 0,
        "total_files": 0,
        "earliest_today": None,
    }

    if not Path(db_path).exists():
        return result

    yest_start, yest_end = _local_day_utc_bounds(yesterday)
    today_start, _ = _local_day_utc_bounds(today_str)

    try:
        with sqlite3.connect(db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row

            # AI prompts per project for yesterday
            ai_rows = conn.execute(
                """
                SELECT COALESCE(ap.project, p.name, 'unknown') AS proj_name,
                       COUNT(*) AS cnt,
                       MAX(ap.timestamp) AS last_ts
                FROM ai_prompts ap
                LEFT JOIN projects p ON ap.project_id = p.id
                WHERE ap.timestamp >= ? AND ap.timestamp < ?
                GROUP BY proj_name
                ORDER BY cnt DESC
                """,
                (yest_start, yest_end),
            ).fetchall()

            # File events per project for yesterday
            file_rows = conn.execute(
                """
                SELECT COALESCE(p.name, 'unknown') AS proj_name,
                       COUNT(*) AS cnt,
                       MAX(fe.timestamp) AS last_ts
                FROM file_events fe
                LEFT JOIN projects p ON fe.project_id = p.id
                WHERE fe.timestamp >= ? AND fe.timestamp < ?
                GROUP BY proj_name
                ORDER BY cnt DESC
                """,
                (yest_start, yest_end),
            ).fetchall()

            # Merge: combine ai + file per project
            ai_map: dict[str, dict] = {}
            for r in ai_rows:
                name = r["proj_name"]
                ai_map[name] = {
                    "name": name,
                    "ai_count": r["cnt"],
                    "file_count": 0,
                    "last_ts": r["last_ts"],
                }
            for r in file_rows:
                name = r["proj_name"]
                if name in ai_map:
                    ai_map[name]["file_count"] = r["cnt"]
                    # keep latest timestamp
                    if r["last_ts"] and (
                        not ai_map[name]["last_ts"]
                        or r["last_ts"] > ai_map[name]["last_ts"]
                    ):
                        ai_map[name]["last_ts"] = r["last_ts"]
                else:
                    ai_map[name] = {
                        "name": name,
                        "ai_count": 0,
                        "file_count": r["cnt"],
                        "last_ts": r["last_ts"],
                    }

            # Sort by total activity descending
            projects_list = sorted(
                ai_map.values(),
                key=lambda x: x["ai_count"] + x["file_count"],
                reverse=True,
            )
            result["projects"] = projects_list
            result["total_ai"] = sum(p["ai_count"] for p in projects_list)
            result["total_files"] = sum(p["file_count"] for p in projects_list)

            # Earliest activity for today
            today_row = conn.execute(
                """
                SELECT MIN(timestamp) AS earliest
                FROM activity_log
                WHERE timestamp >= ?
                """,
                (today_start,),
            ).fetchone()
            if today_row and today_row["earliest"]:
                dt = _to_local(_parse_ts(today_row["earliest"]))
                result["earliest_today"] = dt.strftime("%H:%M") if dt else None

    except Exception as exc:  # noqa: BLE001
        print(f"[morning_briefing] WARNING: DB query failed: {exc}", file=sys.stderr)

    return result


def get_incomplete_todos(vault_path: str, date_str: str) -> list[str]:
    """
    Extract incomplete TODO lines (- [ ] ...) from the Daily Note for date_str.

    Returns a list of todo text strings (without the leading '- [ ] ').
    """
    note_path = Path(vault_path) / "Daily" / f"{date_str}.md"
    if not note_path.exists():
        return []

    todos: list[str] = []
    try:
        content = note_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            # Match both "- [ ] text" and "  - [ ] text" (indented)
            m = re.match(r"^\s*-\s+\[\s+\]\s+(.*)", line)
            if m:
                todos.append(m.group(1).strip())
    except Exception as exc:  # noqa: BLE001
        print(f"[morning_briefing] WARNING: Could not read daily note: {exc}", file=sys.stderr)

    return todos


def get_last_modified_file(db_path: str) -> Optional[dict]:
    """
    Return info about the most recently modified file in file_events.

    Returns a dict with keys: file_path, event_type, timestamp, project_name
    Or None if no records exist.
    """
    if not Path(db_path).exists():
        return None

    try:
        with sqlite3.connect(db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT fe.file_path, fe.event_type, fe.timestamp,
                       COALESCE(p.name, 'unknown') AS project_name
                FROM file_events fe
                LEFT JOIN projects p ON fe.project_id = p.id
                ORDER BY fe.timestamp DESC
                LIMIT 1
                """,
            ).fetchone()
        if row:
            return dict(row)
    except Exception as exc:  # noqa: BLE001
        print(f"[morning_briefing] WARNING: Could not query last file: {exc}", file=sys.stderr)

    return None


def _get_most_recent_project(db_path: str, yesterday: str) -> Optional[dict]:
    """
    Return the project with the latest activity timestamp yesterday.
    Returns dict with keys: name, last_ts
    """
    if not Path(db_path).exists():
        return None

    yest_start, yest_end = _local_day_utc_bounds(yesterday)

    try:
        with sqlite3.connect(db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            # Check ai_prompts for latest
            ai_row = conn.execute(
                """
                SELECT COALESCE(ap.project, p.name, 'unknown') AS proj_name,
                       MAX(ap.timestamp) AS last_ts
                FROM ai_prompts ap
                LEFT JOIN projects p ON ap.project_id = p.id
                WHERE ap.timestamp >= ? AND ap.timestamp < ?
                GROUP BY proj_name
                ORDER BY last_ts DESC
                LIMIT 1
                """,
                (yest_start, yest_end),
            ).fetchone()
            fe_row = conn.execute(
                """
                SELECT COALESCE(p.name, 'unknown') AS proj_name,
                       MAX(fe.timestamp) AS last_ts
                FROM file_events fe
                LEFT JOIN projects p ON fe.project_id = p.id
                WHERE fe.timestamp >= ? AND fe.timestamp < ?
                GROUP BY proj_name
                ORDER BY last_ts DESC
                LIMIT 1
                """,
                (yest_start, yest_end),
            ).fetchone()

        candidates = []
        if ai_row and ai_row["last_ts"]:
            candidates.append({"name": ai_row["proj_name"], "last_ts": ai_row["last_ts"]})
        if fe_row and fe_row["last_ts"]:
            candidates.append({"name": fe_row["proj_name"], "last_ts": fe_row["last_ts"]})

        if not candidates:
            return None

        return max(candidates, key=lambda x: x["last_ts"])

    except Exception as exc:  # noqa: BLE001
        print(f"[morning_briefing] WARNING: Could not query recent project: {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Briefing text generator
# ---------------------------------------------------------------------------

def generate_briefing(data: dict) -> str:
    """
    Generate the morning briefing as a plain text string
    (suitable for both terminal output and vault note body).

    Parameters
    ----------
    data : dict
        Output from assembling get_yesterday_summary(), get_incomplete_todos(),
        get_last_modified_file().
        Expected keys:
            today_str, yesterday_str, projects, todos,
            last_file, most_recent_project, earliest_today
    """
    today_str: str = data.get("today_str", date.today().strftime("%Y-%m-%d"))
    yesterday_str: str = data.get("yesterday_str", "")
    projects: list[dict] = data.get("projects", [])
    todos: list[str] = data.get("todos", [])
    last_file: Optional[dict] = data.get("last_file")
    most_recent: Optional[dict] = data.get("most_recent_project")
    earliest_today: Optional[str] = data.get("earliest_today")

    lines: list[str] = []

    # ── Header ──────────────────────────────────────────────────────────────
    title = f"  DayTracker Morning Briefing  {today_str} "
    width = max(len(title) + 4, 50)
    border = "=" * (width - 2)
    lines.append(f"+{border}+")
    lines.append(f"|{title.center(width - 2)}|")
    lines.append(f"+{border}+")
    lines.append("")

    # ── Yesterday's projects ────────────────────────────────────────────────
    lines.append("[Projects] Yesterday's projects")
    if projects:
        for proj in projects:
            name = proj["name"]
            ai_c = proj["ai_count"]
            file_c = proj["file_count"]
            lines.append(f"  * {name}  (AI {ai_c}건, Files {file_c}건)")
    else:
        lines.append("  (no activity records for yesterday)")
    lines.append("")

    # ── Incomplete TODOs ────────────────────────────────────────────────────
    lines.append("[TODOs] Incomplete items from yesterday's daily note")
    if todos:
        for todo in todos:
            lines.append(f"  * [ ] {todo}")
    else:
        lines.append(f"  (no incomplete TODOs in Daily/{yesterday_str}.md)")
    lines.append("")

    # ── Recommended start ───────────────────────────────────────────────────
    lines.append("[Recommended] Suggested starting task")
    if most_recent:
        proj_name = most_recent["name"]
        last_ts_raw = most_recent.get("last_ts", "")
        last_dt = _to_local(_parse_ts(last_ts_raw))
        last_ts_str = last_dt.strftime("%H:%M") if last_dt else "?"
        lines.append(f"  Most recent project: {proj_name} (yesterday {last_ts_str})")
    if last_file:
        fp = last_file.get("file_path", "")
        # Show relative portion if possible
        fp_display = Path(fp).name if fp else "?"
        lines.append(f"  Last modified file: {fp_display}")
        if fp:
            lines.append(f"    ({fp})")
    if not most_recent and not last_file:
        lines.append("  (no previous activity found)")
    lines.append("")

    # ── Today's status ──────────────────────────────────────────────────────
    lines.append("[Today]")
    if earliest_today:
        lines.append(f"  First activity today: {earliest_today}")
    else:
        lines.append("  No activity records yet for today")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Vault note writer
# ---------------------------------------------------------------------------

def _write_briefing_note(vault_path: str, today_str: str, content: str) -> str:
    """
    Write the briefing to {vault}/Briefings/YYYY-MM-DD-morning.md.
    Returns the path that was written.
    """
    briefings_dir = Path(vault_path) / "Briefings"
    briefings_dir.mkdir(parents=True, exist_ok=True)

    note_path = briefings_dir / f"{today_str}-morning.md"

    frontmatter = (
        f"---\n"
        f"date: {today_str}\n"
        f"type: briefing\n"
        f"tags: [briefing, morning]\n"
        f"---\n"
    )
    heading = f"\n# Morning Briefing {today_str}\n\n"

    # Convert plain-text briefing to Markdown-friendly form
    # Replace box-drawing lines with fenced code or leave as-is (it's readable)
    note_content = frontmatter + heading + content

    note_path.write_text(note_content, encoding="utf-8")
    return str(note_path)


# ---------------------------------------------------------------------------
# Main run function
# ---------------------------------------------------------------------------

def run(dry_run: bool = False) -> None:
    """
    Entry point for the morning briefing agent.

    Parameters
    ----------
    dry_run : bool
        If True, print the briefing but do not write the vault note.
    """
    today_str = date.today().strftime("%Y-%m-%d")
    yesterday_str = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")

    # Load config
    cfg = _cfg
    if cfg is None:
        print("[morning_briefing] ERROR: Could not load config.", file=sys.stderr)
        sys.exit(1)

    try:
        vault_path = cfg.get_vault_path()
    except RuntimeError as exc:
        print(f"[morning_briefing] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    db_path = cfg.get_db_path()

    # Gather data
    summary = get_yesterday_summary(db_path, vault_path)
    todos = get_incomplete_todos(vault_path, yesterday_str)
    last_file = get_last_modified_file(db_path)
    most_recent_project = _get_most_recent_project(db_path, yesterday_str)

    data = {
        "today_str": today_str,
        "yesterday_str": yesterday_str,
        "projects": summary["projects"],
        "todos": todos,
        "last_file": last_file,
        "most_recent_project": most_recent_project,
        "earliest_today": summary["earliest_today"],
    }

    # Generate briefing text
    briefing_text = generate_briefing(data)

    # Print to terminal
    print(briefing_text)

    if dry_run:
        print("[morning_briefing] DRY-RUN: vault note not written.")
        return

    # Write vault note
    note_path = _write_briefing_note(vault_path, today_str, briefing_text)
    print(f"[morning_briefing] Briefing written to: {note_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="DayTracker Morning Briefing Agent - summarises yesterday and recommends today's start.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the briefing to stdout without writing the vault note.",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
