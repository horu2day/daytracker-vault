"""
scripts/obsidian/weekly_note.py - Generate/update the Obsidian Weekly Note.

Writes to {vault}/Weekly/YYYY-Www.md following the CLAUDE.md format.
Week is Monday-based ISO week numbering.

Usage:
    python scripts/obsidian/weekly_note.py [--week YYYY-Www] [--dry-run]
"""

from __future__ import annotations

import argparse
import io
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# Windows console UTF-8 (only wrap if running as __main__)
if sys.platform == "win32" and __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

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
    from config import Config  # type: ignore
    _cfg = Config()

try:
    from scripts.obsidian.writer import write_note, update_section  # type: ignore
except ImportError:
    from obsidian.writer import write_note, update_section  # type: ignore


# ---------------------------------------------------------------------------
# Week helpers
# ---------------------------------------------------------------------------

def _parse_week_str(week_str: str) -> tuple[date, date, str]:
    """
    Parse a YYYY-Www string into (monday, sunday, week_label).

    Returns the Monday and Sunday of the ISO week, and the week label
    in YYYY-Www format (e.g. '2026-W08').
    """
    try:
        # Parse "YYYY-Www" format
        parts = week_str.split("-W")
        if len(parts) != 2:
            raise ValueError(f"Invalid week format: {week_str!r}")
        year = int(parts[0])
        week_num = int(parts[1])
        # ISO week: Monday is day 1
        # date.fromisocalendar(year, week, 1) = that week's Monday
        monday = date.fromisocalendar(year, week_num, 1)
        sunday = monday + timedelta(days=6)
        week_label = f"{year}-W{week_num:02d}"
        return monday, sunday, week_label
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Cannot parse week string '{week_str}': {exc}") from exc


def _current_week() -> tuple[date, date, str]:
    """Return (monday, sunday, week_label) for the current ISO week."""
    today = date.today()
    iso_year, iso_week, _ = today.isocalendar()
    monday = date.fromisocalendar(iso_year, iso_week, 1)
    sunday = monday + timedelta(days=6)
    week_label = f"{iso_year}-W{iso_week:02d}"
    return monday, sunday, week_label


def _week_utc_bounds(monday: date, sunday: date) -> tuple[str, str]:
    """Return (utc_start, utc_end) strings bracketing the local week."""
    local_tz = datetime.now(timezone.utc).astimezone().tzinfo
    local_start = datetime(monday.year, monday.month, monday.day, tzinfo=local_tz)
    local_end = datetime(sunday.year, sunday.month, sunday.day, tzinfo=local_tz) + timedelta(days=1)
    utc_start = local_start.astimezone(timezone.utc)
    utc_end = local_end.astimezone(timezone.utc)
    return (
        utc_start.strftime("%Y-%m-%dT%H:%M:%S"),
        utc_end.strftime("%Y-%m-%dT%H:%M:%S"),
    )


def _day_label_korean(d: date) -> str:
    """Return Korean day-of-week label."""
    labels = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    return labels[d.weekday()]


# ---------------------------------------------------------------------------
# DB queries
# ---------------------------------------------------------------------------

def _query_ai_prompts_week(db_path: str, monday: date, sunday: date) -> list[dict]:
    """Return all ai_prompts records for the given week, ordered by timestamp."""
    utc_start, utc_end = _week_utc_bounds(monday, sunday)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT ap.id, ap.timestamp, ap.tool,
                   COALESCE(p.name, '') AS project,
                   ap.project_id,
                   ap.prompt_text, ap.response_text,
                   ap.input_tokens, ap.output_tokens, ap.session_id
            FROM ai_prompts ap
            LEFT JOIN projects p ON ap.project_id = p.id
            WHERE ap.timestamp >= ? AND ap.timestamp < ?
            ORDER BY ap.timestamp ASC
            """,
            (utc_start, utc_end),
        ).fetchall()
    return [dict(r) for r in rows]


def _query_file_events_week(db_path: str, monday: date, sunday: date) -> list[dict]:
    """Return all file_events records for the given week, ordered by timestamp."""
    utc_start, utc_end = _week_utc_bounds(monday, sunday)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT fe.id, fe.timestamp, fe.file_path, fe.event_type,
                   fe.project_id, fe.file_size,
                   p.name AS project_name
            FROM file_events fe
            LEFT JOIN projects p ON fe.project_id = p.id
            WHERE fe.timestamp >= ? AND fe.timestamp < ?
            ORDER BY fe.timestamp ASC
            """,
            (utc_start, utc_end),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Data aggregation helpers
# ---------------------------------------------------------------------------

def _resolve_tool(row: dict) -> str:
    tool = (row.get("tool") or "").strip()
    return tool if tool else "claude-code"


def _resolve_project(row: dict) -> str:
    return (row.get("project") or "").strip() or "unknown"


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


def _group_ai_by_project(ai_rows: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for row in ai_rows:
        proj = _resolve_project(row)
        groups.setdefault(proj, []).append(row)
    return groups


def _group_file_by_project(file_rows: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for row in file_rows:
        proj = (row.get("project_name") or "").strip() or "unknown"
        groups.setdefault(proj, []).append(row)
    return groups


def _all_projects(
    ai_by_proj: dict[str, list[dict]],
    file_by_proj: dict[str, list[dict]],
) -> list[str]:
    return sorted(set(list(ai_by_proj.keys()) + list(file_by_proj.keys())))


def _tool_counts(ai_rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in ai_rows:
        tool = _resolve_tool(row)
        counts[tool] = counts.get(tool, 0) + 1
    return counts


def _sessions_for_day(ai_rows: list[dict], d: date) -> int:
    """Count AI sessions whose local timestamp falls on the given date."""
    count = 0
    for row in ai_rows:
        dt = _to_local(_parse_ts(row.get("timestamp", "")))
        if dt and dt.date() == d:
            count += 1
    return count


# ---------------------------------------------------------------------------
# Note builders
# ---------------------------------------------------------------------------

def _build_frontmatter(
    week_label: str,
    date_start: str,
    date_end: str,
    projects: list[str],
    total_ai_sessions: int,
    total_file_changes: int,
) -> str:
    proj_yaml = "[" + ", ".join(projects) + "]" if projects else "[]"
    return (
        f"---\n"
        f"week: {week_label}\n"
        f"date_start: {date_start}\n"
        f"date_end: {date_end}\n"
        f"tags: [weekly]\n"
        f"projects: {proj_yaml}\n"
        f"total_ai_sessions: {total_ai_sessions}\n"
        f"total_file_changes: {total_file_changes}\n"
        f"---\n"
    )


def _build_summary_section(
    projects: list[str],
    ai_rows: list[dict],
    file_rows: list[dict],
) -> str:
    n_projects = len(projects)
    n_ai = len(ai_rows)
    n_files = len(file_rows)
    return (
        f"## 요약\n\n"
        f"- **{n_projects}개** 프로젝트에서 작업\n"
        f"- AI 상호작용: **{n_ai}건** 총계\n"
        f"- 생성/수정 파일: **{n_files}개**\n"
    )


def _build_projects_section(
    projects: list[str],
    ai_by_proj: dict[str, list[dict]],
    file_by_proj: dict[str, list[dict]],
) -> str:
    lines = ["## 프로젝트별 활동\n"]

    for proj in projects:
        lines.append(f"\n### {proj}\n\n")
        proj_ai = ai_by_proj.get(proj, [])
        proj_files = file_by_proj.get(proj, [])

        lines.append(f"- AI 세션: {len(proj_ai)}건\n")
        lines.append(f"- 파일 변경: {len(proj_files)}건\n")

        # Top 3 recent AI prompts as summaries
        if proj_ai:
            recent = proj_ai[-3:]  # last 3 by timestamp order
            lines.append("- 주요 작업:\n")
            for row in recent:
                prompt = (row.get("prompt_text") or "").replace("\n", " ").strip()
                summary = prompt[:80] + ("..." if len(prompt) > 80 else "")
                tool = _resolve_tool(row)
                dt = _to_local(_parse_ts(row.get("timestamp", "")))
                time_str = dt.strftime("%m/%d %H:%M") if dt else ""
                lines.append(f"  - `{time_str}` [{tool}] {summary}\n")

    return "".join(lines)


def _build_daily_notes_section(
    monday: date,
    sunday: date,
    ai_rows: list[dict],
) -> str:
    lines = ["## 이번 주 Daily Notes\n\n"]
    current = monday
    while current <= sunday:
        day_label = _day_label_korean(current)
        day_str = current.strftime("%Y-%m-%d")
        n_sessions = _sessions_for_day(ai_rows, current)
        if n_sessions > 0:
            lines.append(
                f"- [[Daily/{day_str}|{day_label}]] - {n_sessions}개 세션\n"
            )
        else:
            lines.append(f"- [[Daily/{day_str}|{day_label}]]\n")
        current += timedelta(days=1)
    return "".join(lines)


def build_weekly_note(
    week_label: str,
    monday: date,
    sunday: date,
    ai_rows: list[dict],
    file_rows: list[dict],
) -> str:
    """Build the complete Weekly Note markdown."""
    ai_by_proj = _group_ai_by_project(ai_rows)
    file_by_proj = _group_file_by_project(file_rows)
    projects = _all_projects(ai_by_proj, file_by_proj)

    date_start = monday.strftime("%Y-%m-%d")
    date_end = sunday.strftime("%Y-%m-%d")

    frontmatter = _build_frontmatter(
        week_label, date_start, date_end, projects,
        len(ai_rows), len(file_rows),
    )
    summary = _build_summary_section(projects, ai_rows, file_rows)
    projects_section = _build_projects_section(projects, ai_by_proj, file_by_proj)
    daily_notes_section = _build_daily_notes_section(monday, sunday, ai_rows)

    note = (
        frontmatter
        + f"\n# {week_label} 주간 작업 요약\n"
        + f"**기간**: {date_start} (월) ~ {date_end} (일)\n\n"
        + summary
        + "\n"
        + projects_section
        + "\n"
        + daily_notes_section
        + "\n"
    )
    return note


# ---------------------------------------------------------------------------
# Main entry: create or update
# ---------------------------------------------------------------------------

def create_or_update_weekly_note(
    week_label: str,
    monday: date,
    sunday: date,
    db_path: str,
    vault_path: str,
    dry_run: bool = False,
) -> str:
    """
    Create or update the Weekly Note for the given week.

    If the note already exists, the auto-generated sections are replaced
    while preserving any manually added content.

    Returns the relative path of the note file.
    """
    relative_path = f"Weekly/{week_label}.md"

    ai_rows = _query_ai_prompts_week(db_path, monday, sunday)
    file_rows = _query_file_events_week(db_path, monday, sunday)

    full_content = build_weekly_note(week_label, monday, sunday, ai_rows, file_rows)

    if dry_run:
        print(f"\n{'='*60}")
        print(f"[DRY-RUN] Would write: {relative_path}")
        print(f"{'='*60}")
        print(full_content)
        return relative_path

    target = Path(vault_path) / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        print(f"[weekly_note] Updating existing note: {relative_path}")
        # Update auto-generated sections, preserving manually added content
        ai_by_proj = _group_ai_by_project(ai_rows)
        file_by_proj = _group_file_by_project(file_rows)
        projects = _all_projects(ai_by_proj, file_by_proj)
        date_start = monday.strftime("%Y-%m-%d")
        date_end = sunday.strftime("%Y-%m-%d")

        # Re-write frontmatter
        import re
        existing = target.read_text(encoding="utf-8")
        new_fm = _build_frontmatter(
            week_label, date_start, date_end, projects,
            len(ai_rows), len(file_rows),
        )
        fm_pattern = re.compile(r"^---\n.*?^---\n", re.DOTALL | re.MULTILINE)
        if fm_pattern.match(existing):
            existing = fm_pattern.sub(new_fm + "\n", existing, count=1)
        else:
            existing = new_fm + "\n" + existing
        target.write_text(existing, encoding="utf-8")

        # Update the auto-generated sections
        new_summary = _build_summary_section(projects, ai_rows, file_rows)
        new_projects = _build_projects_section(projects, ai_by_proj, file_by_proj)
        new_daily = _build_daily_notes_section(monday, sunday, ai_rows)
        update_section(vault_path, relative_path, "## 요약", new_summary)
        update_section(vault_path, relative_path, "## 프로젝트별 활동", new_projects)
        update_section(vault_path, relative_path, "## 이번 주 Daily Notes", new_daily)
    else:
        written = write_note(vault_path, relative_path, full_content, overwrite=True)
        if written:
            print(f"[weekly_note] Created: {relative_path}")
        else:
            print(f"[weekly_note] Already existed, skipped: {relative_path}")

    return relative_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate or update the Obsidian Weekly Note."
    )
    parser.add_argument(
        "--week",
        metavar="YYYY-Www",
        default=None,
        help="ISO week to generate note for (e.g. 2026-W08). Default: current week.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print note content to stdout without writing files.",
    )
    args = parser.parse_args()

    if args.week:
        try:
            monday, sunday, week_label = _parse_week_str(args.week)
        except ValueError as exc:
            print(f"[weekly_note] ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        monday, sunday, week_label = _current_week()

    try:
        vault_path = _cfg.get_vault_path()
    except RuntimeError as exc:
        print(f"[weekly_note] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    db_path = _cfg.get_db_path()

    if not Path(db_path).exists():
        print(
            f"[weekly_note] ERROR: Database not found at {db_path}. "
            "Run: python scripts/init_db.py",
            file=sys.stderr,
        )
        sys.exit(1)

    create_or_update_weekly_note(
        week_label=week_label,
        monday=monday,
        sunday=sunday,
        db_path=db_path,
        vault_path=vault_path,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
