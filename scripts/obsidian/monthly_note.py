"""
scripts/obsidian/monthly_note.py - Generate/update the Obsidian Monthly Note.

Writes to {vault}/Monthly/YYYY-MM.md following the CLAUDE.md format.

Usage:
    python scripts/obsidian/monthly_note.py [--month YYYY-MM] [--dry-run]
"""

from __future__ import annotations

import argparse
import calendar
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
# Month helpers
# ---------------------------------------------------------------------------

def _parse_month_str(month_str: str) -> tuple[date, date, str]:
    """
    Parse a YYYY-MM string into (first_day, last_day, month_label).

    Returns the first and last date of the month, and the label string.
    """
    try:
        parts = month_str.split("-")
        if len(parts) != 2:
            raise ValueError(f"Invalid month format: {month_str!r}")
        year = int(parts[0])
        month = int(parts[1])
        _, days_in_month = calendar.monthrange(year, month)
        first_day = date(year, month, 1)
        last_day = date(year, month, days_in_month)
        label = f"{year}-{month:02d}"
        return first_day, last_day, label
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Cannot parse month string '{month_str}': {exc}") from exc


def _current_month() -> tuple[date, date, str]:
    """Return (first_day, last_day, month_label) for the current month."""
    today = date.today()
    _, days_in_month = calendar.monthrange(today.year, today.month)
    first_day = date(today.year, today.month, 1)
    last_day = date(today.year, today.month, days_in_month)
    label = f"{today.year}-{today.month:02d}"
    return first_day, last_day, label


def _month_utc_bounds(first_day: date, last_day: date) -> tuple[str, str]:
    """Return (utc_start, utc_end) strings bracketing the local month."""
    local_tz = datetime.now(timezone.utc).astimezone().tzinfo
    local_start = datetime(first_day.year, first_day.month, first_day.day, tzinfo=local_tz)
    local_end = datetime(last_day.year, last_day.month, last_day.day, tzinfo=local_tz) + timedelta(days=1)
    utc_start = local_start.astimezone(timezone.utc)
    utc_end = local_end.astimezone(timezone.utc)
    return (
        utc_start.strftime("%Y-%m-%dT%H:%M:%S"),
        utc_end.strftime("%Y-%m-%dT%H:%M:%S"),
    )


def _iso_weeks_in_month(first_day: date, last_day: date) -> list[tuple[int, int, str]]:
    """
    Return a list of (iso_year, iso_week, week_label) tuples for ISO weeks
    that overlap with the given month.  Ordered, deduplicated.
    """
    seen: list[tuple[int, int, str]] = []
    seen_set: set[tuple[int, int]] = set()
    current = first_day
    while current <= last_day:
        iso_year, iso_week, _ = current.isocalendar()
        key = (iso_year, iso_week)
        if key not in seen_set:
            seen_set.add(key)
            label = f"{iso_year}-W{iso_week:02d}"
            seen.append((iso_year, iso_week, label))
        current += timedelta(days=1)
    return seen


# ---------------------------------------------------------------------------
# DB queries
# ---------------------------------------------------------------------------

def _query_ai_prompts_month(db_path: str, first_day: date, last_day: date) -> list[dict]:
    """Return all ai_prompts records for the given month, ordered by timestamp."""
    utc_start, utc_end = _month_utc_bounds(first_day, last_day)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, timestamp, tool, project, project_id,
                   prompt_text, response_text,
                   input_tokens, output_tokens, session_id
            FROM ai_prompts
            WHERE timestamp >= ? AND timestamp < ?
            ORDER BY timestamp ASC
            """,
            (utc_start, utc_end),
        ).fetchall()
    return [dict(r) for r in rows]


def _query_file_events_month(db_path: str, first_day: date, last_day: date) -> list[dict]:
    """Return all file_events records for the given month, ordered by timestamp."""
    utc_start, utc_end = _month_utc_bounds(first_day, last_day)
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


def _active_days(ai_rows: list[dict], file_rows: list[dict]) -> int:
    """Count the number of distinct local calendar days with any activity."""
    days: set[date] = set()
    for row in ai_rows:
        dt = _to_local(_parse_ts(row.get("timestamp", "")))
        if dt:
            days.add(dt.date())
    for row in file_rows:
        dt = _to_local(_parse_ts(row.get("timestamp", "")))
        if dt:
            days.add(dt.date())
    return len(days)


def _sessions_for_week(ai_rows: list[dict], iso_year: int, iso_week: int) -> int:
    """Count AI sessions in the given ISO week."""
    count = 0
    for row in ai_rows:
        dt = _to_local(_parse_ts(row.get("timestamp", "")))
        if dt:
            iy, iw, _ = dt.date().isocalendar()
            if iy == iso_year and iw == iso_week:
                count += 1
    return count


def _activity_days_for_project(
    proj: str,
    ai_by_proj: dict[str, list[dict]],
    file_by_proj: dict[str, list[dict]],
) -> int:
    """Count distinct days with activity for a specific project."""
    days: set[date] = set()
    for row in ai_by_proj.get(proj, []):
        dt = _to_local(_parse_ts(row.get("timestamp", "")))
        if dt:
            days.add(dt.date())
    for row in file_by_proj.get(proj, []):
        dt = _to_local(_parse_ts(row.get("timestamp", "")))
        if dt:
            days.add(dt.date())
    return len(days)


# ---------------------------------------------------------------------------
# Note builders
# ---------------------------------------------------------------------------

def _build_frontmatter(
    month_label: str,
    projects: list[str],
    total_ai_sessions: int,
    total_file_changes: int,
) -> str:
    proj_yaml = "[" + ", ".join(projects) + "]" if projects else "[]"
    return (
        f"---\n"
        f"month: {month_label}\n"
        f"tags: [monthly]\n"
        f"projects: {proj_yaml}\n"
        f"total_ai_sessions: {total_ai_sessions}\n"
        f"total_file_changes: {total_file_changes}\n"
        f"---\n"
    )


def _build_stats_section(
    ai_rows: list[dict],
    file_rows: list[dict],
    projects: list[str],
    n_active_days: int,
) -> str:
    tool_counts = _tool_counts(ai_rows)
    tool_detail = ", ".join(
        f"{tool}: {cnt}" for tool, cnt in sorted(tool_counts.items())
    )
    if not tool_detail:
        tool_detail = "0"

    return (
        f"## 통계\n\n"
        f"- 작업일: {n_active_days}일\n"
        f"- 프로젝트: {len(projects)}개\n"
        f"- AI 세션: {len(ai_rows)}건 ({tool_detail})\n"
        f"- 파일 변경: {len(file_rows)}건\n"
    )


def _build_project_table_section(
    projects: list[str],
    ai_by_proj: dict[str, list[dict]],
    file_by_proj: dict[str, list[dict]],
) -> str:
    lines = [
        "## 프로젝트별 집계\n\n",
        "| 프로젝트 | AI 세션 | 파일 변경 | 활동일 |\n",
        "|---------|---------|---------|------|\n",
    ]
    for proj in projects:
        n_ai = len(ai_by_proj.get(proj, []))
        n_files = len(file_by_proj.get(proj, []))
        n_days = _activity_days_for_project(proj, ai_by_proj, file_by_proj)
        lines.append(f"| {proj} | {n_ai} | {n_files} | {n_days} |\n")
    return "".join(lines)


def _build_weekly_summary_section(
    ai_rows: list[dict],
    weeks: list[tuple[int, int, str]],
) -> str:
    lines = ["## 주간 요약\n\n"]
    for iso_year, iso_week, week_label in weeks:
        n_sessions = _sessions_for_week(ai_rows, iso_year, iso_week)
        lines.append(
            f"- [[Weekly/{week_label}|{week_label}]] - {n_sessions}세션\n"
        )
    return "".join(lines)


def _build_daily_notes_dataview_section(month_label: str) -> str:
    """Build the Dataview block for browsing daily notes in this month."""
    # Extract year and month for the query date bounds
    year, month_num = month_label.split("-")
    _, days_in_month = calendar.monthrange(int(year), int(month_num))
    date_start = f"{month_label}-01"
    date_end = f"{month_label}-{days_in_month:02d}"

    return (
        "## 이달의 Daily Notes\n\n"
        "```dataview\n"
        "TABLE work_start, total_ai_sessions\n"
        'FROM "Daily"\n'
        f'WHERE date >= date("{date_start}") AND date <= date("{date_end}")\n'
        "SORT date ASC\n"
        "```\n"
    )


def build_monthly_note(
    month_label: str,
    first_day: date,
    last_day: date,
    ai_rows: list[dict],
    file_rows: list[dict],
) -> str:
    """Build the complete Monthly Note markdown."""
    ai_by_proj = _group_ai_by_project(ai_rows)
    file_by_proj = _group_file_by_project(file_rows)
    projects = _all_projects(ai_by_proj, file_by_proj)
    n_active_days = _active_days(ai_rows, file_rows)
    weeks = _iso_weeks_in_month(first_day, last_day)

    # Parse year/month for the title
    year_str, month_str = month_label.split("-")
    title = f"{year_str}년 {int(month_str)}월 작업 요약"

    frontmatter = _build_frontmatter(
        month_label, projects, len(ai_rows), len(file_rows)
    )
    stats = _build_stats_section(ai_rows, file_rows, projects, n_active_days)
    project_table = _build_project_table_section(projects, ai_by_proj, file_by_proj)
    weekly_summary = _build_weekly_summary_section(ai_rows, weeks)
    daily_dataview = _build_daily_notes_dataview_section(month_label)

    note = (
        frontmatter
        + f"\n# {title}\n\n"
        + stats
        + "\n"
        + project_table
        + "\n"
        + weekly_summary
        + "\n"
        + daily_dataview
    )
    return note


# ---------------------------------------------------------------------------
# Main entry: create or update
# ---------------------------------------------------------------------------

def create_or_update_monthly_note(
    month_label: str,
    first_day: date,
    last_day: date,
    db_path: str,
    vault_path: str,
    dry_run: bool = False,
) -> str:
    """
    Create or update the Monthly Note for the given month.

    If the note already exists, the auto-generated sections are replaced
    while preserving any manually added content.

    Returns the relative path of the note file.
    """
    relative_path = f"Monthly/{month_label}.md"

    ai_rows = _query_ai_prompts_month(db_path, first_day, last_day)
    file_rows = _query_file_events_month(db_path, first_day, last_day)

    full_content = build_monthly_note(month_label, first_day, last_day, ai_rows, file_rows)

    if dry_run:
        print(f"\n{'='*60}")
        print(f"[DRY-RUN] Would write: {relative_path}")
        print(f"{'='*60}")
        print(full_content)
        return relative_path

    target = Path(vault_path) / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        print(f"[monthly_note] Updating existing note: {relative_path}")
        # Rebuild data
        ai_by_proj = _group_ai_by_project(ai_rows)
        file_by_proj = _group_file_by_project(file_rows)
        projects = _all_projects(ai_by_proj, file_by_proj)
        n_active_days = _active_days(ai_rows, file_rows)
        weeks = _iso_weeks_in_month(first_day, last_day)

        # Re-write frontmatter
        import re
        existing = target.read_text(encoding="utf-8")
        new_fm = _build_frontmatter(month_label, projects, len(ai_rows), len(file_rows))
        fm_pattern = re.compile(r"^---\n.*?^---\n", re.DOTALL | re.MULTILINE)
        if fm_pattern.match(existing):
            existing = fm_pattern.sub(new_fm + "\n", existing, count=1)
        else:
            existing = new_fm + "\n" + existing
        target.write_text(existing, encoding="utf-8")

        # Update auto-generated sections
        new_stats = _build_stats_section(ai_rows, file_rows, projects, n_active_days)
        new_table = _build_project_table_section(projects, ai_by_proj, file_by_proj)
        new_weekly = _build_weekly_summary_section(ai_rows, weeks)
        new_daily = _build_daily_notes_dataview_section(month_label)
        update_section(vault_path, relative_path, "## 통계", new_stats)
        update_section(vault_path, relative_path, "## 프로젝트별 집계", new_table)
        update_section(vault_path, relative_path, "## 주간 요약", new_weekly)
        update_section(vault_path, relative_path, "## 이달의 Daily Notes", new_daily)
    else:
        written = write_note(vault_path, relative_path, full_content, overwrite=True)
        if written:
            print(f"[monthly_note] Created: {relative_path}")
        else:
            print(f"[monthly_note] Already existed, skipped: {relative_path}")

    return relative_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate or update the Obsidian Monthly Note."
    )
    parser.add_argument(
        "--month",
        metavar="YYYY-MM",
        default=None,
        help="Month to generate note for (e.g. 2026-02). Default: current month.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print note content to stdout without writing files.",
    )
    args = parser.parse_args()

    if args.month:
        try:
            first_day, last_day, month_label = _parse_month_str(args.month)
        except ValueError as exc:
            print(f"[monthly_note] ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        first_day, last_day, month_label = _current_month()

    try:
        vault_path = _cfg.get_vault_path()
    except RuntimeError as exc:
        print(f"[monthly_note] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    db_path = _cfg.get_db_path()

    if not Path(db_path).exists():
        print(
            f"[monthly_note] ERROR: Database not found at {db_path}. "
            "Run: python scripts/init_db.py",
            file=sys.stderr,
        )
        sys.exit(1)

    create_or_update_monthly_note(
        month_label=month_label,
        first_day=first_day,
        last_day=last_day,
        db_path=db_path,
        vault_path=vault_path,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
