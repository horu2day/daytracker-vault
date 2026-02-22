"""
scripts/agents/weekly_review.py - DayTracker Phase 6 Weekly Review Agent.

Generates a weekly work review by querying worklog.db for the given ISO week,
calculating statistics and highlights, then:
  1. Printing a formatted report to the terminal.
  2. Writing/updating {vault}/Weekly/YYYY-Www.md with a "## 주간 리뷰" section.

CLI:
    python scripts/agents/weekly_review.py [--week YYYY-Www] [--dry-run]

Triggered by watcher_daemon.py every Friday at 18:00.

Sample output:
    +----------------------------------------------+
    |  Weekly Review: 2026-W08 (02/16~02/22)       |
    +----------------------------------------------+

    이번 주 통계
      작업일: 2일 | 프로젝트: 2개
      AI 세션: 31건 (claude-code: 31)
      파일 변경: 430건

    하이라이트
      - 가장 활발한 날: 2026-02-22 (AI 6건, 파일 180건)
      - 가장 많이 작업한 프로젝트: daytracker-vault (31 AI 세션)
      - 가장 큰 변화: scripts/watcher_daemon.py (5회 수정)

    다음 주 추천
      - daytracker-vault: 계속 진행 (마지막 작업 22일)
"""

from __future__ import annotations

import argparse
import io
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
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

try:
    from scripts.obsidian.writer import update_section  # type: ignore
except ImportError:
    try:
        from obsidian.writer import update_section  # type: ignore
    except ImportError:
        update_section = None  # type: ignore


# ---------------------------------------------------------------------------
# Week helpers
# ---------------------------------------------------------------------------

def _parse_week_str(week_str: str) -> tuple[date, date, str]:
    """
    Parse a YYYY-Www string into (monday, sunday, week_label).
    """
    try:
        parts = week_str.split("-W")
        if len(parts) != 2:
            raise ValueError(f"Invalid week format: {week_str!r}")
        year = int(parts[0])
        week_num = int(parts[1])
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
    local_end = (
        datetime(sunday.year, sunday.month, sunday.day, tzinfo=local_tz)
        + timedelta(days=1)
    )
    utc_start = local_start.astimezone(timezone.utc)
    utc_end = local_end.astimezone(timezone.utc)
    return (
        utc_start.strftime("%Y-%m-%dT%H:%M:%S"),
        utc_end.strftime("%Y-%m-%dT%H:%M:%S"),
    )


def _parse_ts(ts_str: str) -> Optional[datetime]:
    if not ts_str:
        return None
    try:
        ts_str = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(ts_str)
    except (ValueError, AttributeError):
        try:
            return datetime.strptime(ts_str[:19], "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return None


def _to_local(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.astimezone()


# ---------------------------------------------------------------------------
# DB query functions
# ---------------------------------------------------------------------------

def get_week_stats(db_path: str, week_str: str) -> dict:
    """
    Query worklog.db and return a statistics dict for the given week.

    Returns
    -------
    dict with keys:
        week_label, date_start, date_end,
        work_days (set of date strings),
        n_projects, projects (list of names),
        n_ai_sessions, ai_tool_counts (dict tool->count),
        n_file_changes,
        ai_rows (list of raw dicts),
        file_rows (list of raw dicts),
        project_ai_counts (dict project->count),
        project_file_counts (dict project->count),
    """
    monday, sunday, week_label = _parse_week_str(week_str)
    utc_start, utc_end = _week_utc_bounds(monday, sunday)

    result: dict = {
        "week_label": week_label,
        "date_start": monday.strftime("%Y-%m-%d"),
        "date_end": sunday.strftime("%Y-%m-%d"),
        "monday": monday,
        "sunday": sunday,
    }

    db = Path(db_path)
    if not db.exists():
        result.update({
            "work_days": set(),
            "n_projects": 0,
            "projects": [],
            "n_ai_sessions": 0,
            "ai_tool_counts": {},
            "n_file_changes": 0,
            "ai_rows": [],
            "file_rows": [],
            "project_ai_counts": {},
            "project_file_counts": {},
        })
        return result

    try:
        with sqlite3.connect(str(db), timeout=5) as conn:
            conn.row_factory = sqlite3.Row

            # AI prompts
            ai_rows = conn.execute(
                """
                SELECT ap.id, ap.timestamp, ap.tool, ap.project_id,
                       ap.prompt_text, ap.response_text,
                       ap.input_tokens, ap.output_tokens, ap.session_id,
                       p.name AS project_name
                FROM ai_prompts ap
                LEFT JOIN projects p ON ap.project_id = p.id
                WHERE ap.timestamp >= ? AND ap.timestamp < ?
                ORDER BY ap.timestamp ASC
                """,
                (utc_start, utc_end),
            ).fetchall()
            ai_rows = [dict(r) for r in ai_rows]

            # File events
            file_rows = conn.execute(
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
            file_rows = [dict(r) for r in file_rows]

    except sqlite3.Error as exc:
        print(f"[weekly_review] DB error: {exc}", file=sys.stderr)
        ai_rows = []
        file_rows = []

    # Compute stats
    work_days: set[str] = set()
    for row in ai_rows:
        dt = _to_local(_parse_ts(row.get("timestamp", "")))
        if dt:
            work_days.add(dt.strftime("%Y-%m-%d"))
    for row in file_rows:
        dt = _to_local(_parse_ts(row.get("timestamp", "")))
        if dt:
            work_days.add(dt.strftime("%Y-%m-%d"))

    ai_tool_counts: dict[str, int] = {}
    project_ai: dict[str, int] = {}
    for row in ai_rows:
        tool = (row.get("tool") or "claude-code").strip() or "claude-code"
        ai_tool_counts[tool] = ai_tool_counts.get(tool, 0) + 1
        proj = (row.get("project_name") or "unknown").strip() or "unknown"
        project_ai[proj] = project_ai.get(proj, 0) + 1

    project_files: dict[str, int] = {}
    for row in file_rows:
        proj = (row.get("project_name") or "unknown").strip() or "unknown"
        project_files[proj] = project_files.get(proj, 0) + 1

    all_projects = sorted(set(list(project_ai.keys()) + list(project_files.keys())))

    result.update({
        "work_days": work_days,
        "n_projects": len(all_projects),
        "projects": all_projects,
        "n_ai_sessions": len(ai_rows),
        "ai_tool_counts": ai_tool_counts,
        "n_file_changes": len(file_rows),
        "ai_rows": ai_rows,
        "file_rows": file_rows,
        "project_ai_counts": project_ai,
        "project_file_counts": project_files,
    })
    return result


def find_highlights(db_path: str, week_str: str) -> dict:
    """
    Find highlight moments for the week.

    Returns
    -------
    dict with keys:
        most_active_day (str YYYY-MM-DD or None),
        most_active_day_ai (int),
        most_active_day_files (int),
        top_project (str or None),
        top_project_ai_count (int),
        most_modified_file (str or None),
        most_modified_file_count (int),
        last_worked_projects (dict project -> last date str),
    """
    stats = get_week_stats(db_path, week_str)
    ai_rows = stats.get("ai_rows", [])
    file_rows = stats.get("file_rows", [])

    # Most active day: day with most total events (ai + file)
    day_ai_count: dict[str, int] = defaultdict(int)
    for row in ai_rows:
        dt = _to_local(_parse_ts(row.get("timestamp", "")))
        if dt:
            day_ai_count[dt.strftime("%Y-%m-%d")] += 1

    day_file_count: dict[str, int] = defaultdict(int)
    for row in file_rows:
        dt = _to_local(_parse_ts(row.get("timestamp", "")))
        if dt:
            day_file_count[dt.strftime("%Y-%m-%d")] += 1

    all_days = set(list(day_ai_count.keys()) + list(day_file_count.keys()))
    most_active_day: Optional[str] = None
    most_active_day_ai = 0
    most_active_day_files = 0
    if all_days:
        most_active_day = max(
            all_days,
            key=lambda d: day_ai_count.get(d, 0) + day_file_count.get(d, 0),
        )
        most_active_day_ai = day_ai_count.get(most_active_day, 0)
        most_active_day_files = day_file_count.get(most_active_day, 0)

    # Top project by AI sessions
    project_ai = stats.get("project_ai_counts", {})
    top_project: Optional[str] = None
    top_project_ai_count = 0
    if project_ai:
        top_project = max(project_ai, key=lambda p: project_ai[p])
        top_project_ai_count = project_ai[top_project]

    # Most frequently modified file
    file_path_counts: dict[str, int] = Counter(
        row.get("file_path", "") for row in file_rows if row.get("file_path")
    )
    most_modified_file: Optional[str] = None
    most_modified_file_count = 0
    if file_path_counts:
        most_modified_file = max(file_path_counts, key=lambda f: file_path_counts[f])
        most_modified_file_count = file_path_counts[most_modified_file]

    # Last worked date per project
    last_worked: dict[str, str] = {}
    for row in ai_rows:
        proj = (row.get("project_name") or "unknown").strip() or "unknown"
        dt = _to_local(_parse_ts(row.get("timestamp", "")))
        if dt:
            day_str = dt.strftime("%Y-%m-%d")
            if proj not in last_worked or day_str > last_worked[proj]:
                last_worked[proj] = day_str
    for row in file_rows:
        proj = (row.get("project_name") or "unknown").strip() or "unknown"
        dt = _to_local(_parse_ts(row.get("timestamp", "")))
        if dt:
            day_str = dt.strftime("%Y-%m-%d")
            if proj not in last_worked or day_str > last_worked[proj]:
                last_worked[proj] = day_str

    return {
        "most_active_day": most_active_day,
        "most_active_day_ai": most_active_day_ai,
        "most_active_day_files": most_active_day_files,
        "top_project": top_project,
        "top_project_ai_count": top_project_ai_count,
        "most_modified_file": most_modified_file,
        "most_modified_file_count": most_modified_file_count,
        "last_worked_projects": last_worked,
    }


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_review(stats: dict, highlights: dict) -> str:
    """
    Build the formatted weekly review report string.
    """
    week_label = stats.get("week_label", "N/A")
    date_start = stats.get("date_start", "")
    date_end = stats.get("date_end", "")

    # Date range as MM/DD
    try:
        ds = datetime.strptime(date_start, "%Y-%m-%d")
        de = datetime.strptime(date_end, "%Y-%m-%d")
        date_range = f"{ds.strftime('%m/%d')}~{de.strftime('%m/%d')}"
    except ValueError:
        date_range = f"{date_start}~{date_end}"

    n_work_days = len(stats.get("work_days", set()))
    n_projects = stats.get("n_projects", 0)
    n_ai = stats.get("n_ai_sessions", 0)
    n_files = stats.get("n_file_changes", 0)
    ai_tool_counts = stats.get("ai_tool_counts", {})

    # Header box
    title = f"Weekly Review: {week_label} ({date_range})"
    box_width = max(len(title) + 6, 50)
    border = "+" + "-" * (box_width - 2) + "+"
    title_line = "|  " + title + " " * (box_width - 4 - len(title)) + "|"

    lines = [
        border,
        title_line,
        border,
        "",
    ]

    # Stats section
    lines.append("이번 주 통계")
    lines.append(f"  작업일: {n_work_days}일 | 프로젝트: {n_projects}개")

    # AI sessions breakdown
    if ai_tool_counts:
        tool_breakdown = ", ".join(
            f"{tool}: {count}" for tool, count in sorted(ai_tool_counts.items())
        )
        lines.append(f"  AI 세션: {n_ai}건 ({tool_breakdown})")
    else:
        lines.append(f"  AI 세션: {n_ai}건")
    lines.append(f"  파일 변경: {n_files}건")
    lines.append("")

    # Highlights
    lines.append("하이라이트")
    most_active_day = highlights.get("most_active_day")
    if most_active_day:
        ai_cnt = highlights.get("most_active_day_ai", 0)
        file_cnt = highlights.get("most_active_day_files", 0)
        lines.append(
            f"  - 가장 활발한 날: {most_active_day} "
            f"(AI {ai_cnt}건, 파일 {file_cnt}건)"
        )
    else:
        lines.append("  - 가장 활발한 날: (데이터 없음)")

    top_project = highlights.get("top_project")
    if top_project:
        top_ai = highlights.get("top_project_ai_count", 0)
        lines.append(
            f"  - 가장 많이 작업한 프로젝트: {top_project} ({top_ai} AI 세션)"
        )
    else:
        lines.append("  - 가장 많이 작업한 프로젝트: (데이터 없음)")

    most_file = highlights.get("most_modified_file")
    if most_file:
        file_cnt = highlights.get("most_modified_file_count", 0)
        # Show just last two path components for readability
        p = Path(most_file)
        display_path = str(Path(*p.parts[-2:])) if len(p.parts) >= 2 else most_file
        lines.append(
            f"  - 가장 큰 변화: {display_path} ({file_cnt}회 수정)"
        )
    lines.append("")

    # Recommendations
    lines.append("다음 주 추천")
    last_worked = highlights.get("last_worked_projects", {})
    if last_worked:
        # Sort by last date descending, show top 3
        sorted_projects = sorted(last_worked.items(), key=lambda x: x[1], reverse=True)
        for proj, last_date in sorted_projects[:3]:
            # Parse date to get day of month
            try:
                last_day = datetime.strptime(last_date, "%Y-%m-%d").day
                lines.append(f"  - {proj}: 계속 진행 (마지막 작업 {last_day}일)")
            except ValueError:
                lines.append(f"  - {proj}: 계속 진행 (마지막: {last_date})")
    else:
        lines.append("  (이번 주 활동 없음)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Vault note updater
# ---------------------------------------------------------------------------

def update_weekly_note(
    vault_path: str,
    week_str: str,
    review_text: str,
) -> None:
    """
    Append/update the "## 주간 리뷰" section in {vault}/Weekly/YYYY-Www.md.
    Creates the file if it does not exist.
    """
    monday, sunday, week_label = _parse_week_str(week_str)
    relative_path = f"Weekly/{week_label}.md"
    note_path = Path(vault_path) / relative_path
    note_path.parent.mkdir(parents=True, exist_ok=True)

    # The section content
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    section_header = "## 주간 리뷰"
    section_body = (
        f"{section_header}\n\n"
        f"> 생성: {generated_at}\n\n"
        "```\n"
        + review_text
        + "\n```\n"
    )

    if update_section is not None:
        success = update_section(vault_path, relative_path, section_header, section_body)
        if success:
            print(f"[weekly_review] Updated '주간 리뷰' section in: {note_path}")
        else:
            print(
                f"[weekly_review] WARNING: Failed to update section in {note_path}",
                file=sys.stderr,
            )
    else:
        # Fallback: just append
        if note_path.exists():
            with open(note_path, "a", encoding="utf-8") as fh:
                fh.write(f"\n\n{section_body}")
            print(f"[weekly_review] Appended '주간 리뷰' to: {note_path}")
        else:
            # Create a minimal note
            date_start = monday.strftime("%Y-%m-%d")
            date_end = sunday.strftime("%Y-%m-%d")
            frontmatter = (
                f"---\nweek: {week_label}\n"
                f"date_start: {date_start}\ndate_end: {date_end}\n"
                f"tags: [weekly]\n---\n\n"
                f"# {week_label} 주간 작업 요약\n\n"
            )
            note_path.write_text(frontmatter + section_body, encoding="utf-8")
            print(f"[weekly_review] Created note with '주간 리뷰': {note_path}")


# ---------------------------------------------------------------------------
# Main run function
# ---------------------------------------------------------------------------

def run(
    week: Optional[str] = None,
    dry_run: bool = False,
    config=None,
) -> None:
    """
    Run the weekly review agent.

    Parameters
    ----------
    week:
        ISO week string like '2026-W08'. Defaults to current week.
    dry_run:
        If True, only print to terminal; do not write to vault.
    config:
        Optional Config instance.
    """
    if config is None:
        config = _cfg

    if config is None:
        print("[weekly_review] ERROR: No config available.", file=sys.stderr)
        return

    # Resolve week
    if week:
        try:
            monday, sunday, week_label = _parse_week_str(week)
            week_str = week_label
        except ValueError as exc:
            print(f"[weekly_review] ERROR: {exc}", file=sys.stderr)
            return
    else:
        monday, sunday, week_label = _current_week()
        week_str = week_label

    try:
        db_path = config.get_db_path()
    except Exception as exc:
        print(f"[weekly_review] ERROR getting db_path: {exc}", file=sys.stderr)
        return

    if not Path(db_path).exists():
        print(
            f"[weekly_review] DB not found at {db_path}. "
            "Run: python scripts/init_db.py",
            file=sys.stderr,
        )
        return

    print(f"[weekly_review] Generating review for {week_str}...")

    stats = get_week_stats(db_path, week_str)
    highlights = find_highlights(db_path, week_str)
    review_text = generate_review(stats, highlights)

    # Print to terminal
    print("\n" + review_text + "\n")

    if dry_run:
        print("[weekly_review] Dry-run mode: vault not updated.")
        return

    # Update vault note
    try:
        vault_path = config.get_vault_path()
    except RuntimeError as exc:
        print(f"[weekly_review] WARNING: {exc}", file=sys.stderr)
        return

    update_weekly_note(vault_path, week_str, review_text)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="DayTracker Weekly Review Agent. Summarises the week's activity."
    )
    parser.add_argument(
        "--week",
        metavar="YYYY-Www",
        default=None,
        help="ISO week to review (e.g. 2026-W08). Default: current week.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print report to terminal only; do not write to vault.",
    )
    args = parser.parse_args()

    run(week=args.week, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
