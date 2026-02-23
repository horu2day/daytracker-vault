"""
scripts/obsidian/daily_note.py - Generate/update the Obsidian Daily Note.

Writes to {vault}/Daily/YYYY-MM-DD.md following the CLAUDE.md format.

If the file already exists, only the "## 타임라인" and "## 프로젝트별 작업"
sections are overwritten; all other content is preserved.

Usage:
    python scripts/obsidian/daily_note.py [--date YYYY-MM-DD] [--dry-run]
"""

from __future__ import annotations

import argparse
import io
import sqlite3
import sys
from datetime import datetime, timezone
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
# Timestamp helpers
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
    """
    Return (utc_start, utc_end) strings bracketing the local calendar day.
    """
    from datetime import timedelta
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
# DB queries
# ---------------------------------------------------------------------------

def _query_ai_prompts(db_path: str, date_str: str) -> list[dict]:
    """Return all ai_prompts records for the given local date, ordered by timestamp."""
    utc_start, utc_end = _local_day_utc_bounds(date_str)
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


def _query_file_events(db_path: str, date_str: str) -> list[dict]:
    """Return all file_events records for the given local date, ordered by timestamp."""
    utc_start, utc_end = _local_day_utc_bounds(date_str)
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


def _group_ai_by_project(ai_rows: list[dict]) -> dict[str, list[dict]]:
    """Group ai_prompts rows by project name."""
    groups: dict[str, list[dict]] = {}
    for row in ai_rows:
        proj = _resolve_project(row)
        groups.setdefault(proj, []).append(row)
    return groups


def _group_file_by_project(file_rows: list[dict]) -> dict[str, list[dict]]:
    """Group file_events rows by project name."""
    groups: dict[str, list[dict]] = {}
    for row in file_rows:
        proj = (row.get("project_name") or "").strip() or "unknown"
        groups.setdefault(proj, []).append(row)
    return groups


def _all_projects(
    ai_by_proj: dict[str, list[dict]],
    file_by_proj: dict[str, list[dict]],
) -> list[str]:
    """Return sorted list of all project names seen today."""
    return sorted(set(list(ai_by_proj.keys()) + list(file_by_proj.keys())))


def _overall_bounds(
    ai_rows: list[dict],
    file_rows: list[dict],
) -> tuple[Optional[str], Optional[str]]:
    """Return (work_start, work_end) in HH:MM format (local time)."""
    all_ts: list[datetime] = []
    for row in ai_rows:
        dt = _to_local(_parse_ts(row.get("timestamp", "")))
        if dt:
            all_ts.append(dt)
    for row in file_rows:
        dt = _to_local(_parse_ts(row.get("timestamp", "")))
        if dt:
            all_ts.append(dt)

    if not all_ts:
        return None, None

    work_start = min(all_ts).strftime("%H:%M")
    work_end = max(all_ts).strftime("%H:%M")
    return work_start, work_end


def _tool_counts(ai_rows: list[dict]) -> dict[str, int]:
    """Count ai_prompts by tool name."""
    counts: dict[str, int] = {}
    for row in ai_rows:
        tool = _resolve_tool(row)
        counts[tool] = counts.get(tool, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Note builders
# ---------------------------------------------------------------------------

def _build_summary_section(
    date_str: str,
    projects: list[str],
    ai_rows: list[dict],
    file_rows: list[dict],
) -> str:
    """Build the ## 요약 section content."""
    n_projects = len(projects)
    n_ai = len(ai_rows)
    n_files = len(file_rows)
    tool_counts = _tool_counts(ai_rows)

    tool_detail = ", ".join(f"{tool}: {cnt}" for tool, cnt in sorted(tool_counts.items()))
    if not tool_detail:
        tool_detail = "0"

    return (
        f"## 요약\n\n"
        f"- **{n_projects}개** 프로젝트에서 작업\n"
        f"- AI 상호작용: **{n_ai}건** ({tool_detail})\n"
        f"- 생성/수정 파일: **{n_files}개**\n"
    )


def _build_timeline_section(
    date_str: str,
    ai_rows: list[dict],
    file_rows: list[dict],
) -> str:
    """Build the ## 타임라인 section."""
    # Combine AI prompt events and file events into a unified timeline
    events: list[tuple[datetime, str, str]] = []  # (dt, project, description)

    for row in ai_rows:
        dt = _to_local(_parse_ts(row.get("timestamp", "")))
        if dt is None:
            continue
        proj = _resolve_project(row)
        tool = _resolve_tool(row)
        prompt = (row.get("prompt_text") or "").replace("\n", " ").strip()
        prompt_preview = prompt[:60] + ("..." if len(prompt) > 60 else "")
        description = f"{tool}: {prompt_preview}" if prompt_preview else tool
        events.append((dt, proj, description))

    for row in file_rows:
        dt = _to_local(_parse_ts(row.get("timestamp", "")))
        if dt is None:
            continue
        proj = (row.get("project_name") or "").strip() or "unknown"
        file_path = row.get("file_path") or ""
        event_type = row.get("event_type") or "변경"
        description = f"{file_path} ({event_type})"
        events.append((dt, proj, description))

    events.sort(key=lambda x: x[0])

    rows_md = ""
    for dt, proj, desc in events:
        time_str = dt.strftime("%H:%M")
        proj_link = f"[[Projects/{proj}|{proj}]]"
        rows_md += f"| {time_str} | {proj_link} | {desc} |\n"

    if not rows_md:
        rows_md = "| - | - | 데이터 없음 |\n"

    return (
        "## 타임라인\n\n"
        "| 시간 | 프로젝트 | 작업 내용 |\n"
        "|------|---------|----------|\n"
        + rows_md
    )


def _build_projects_section(
    date_str: str,
    projects: list[str],
    ai_by_proj: dict[str, list[dict]],
    file_by_proj: dict[str, list[dict]],
) -> str:
    """Build the ## 프로젝트별 작업 section."""
    lines = ["## 프로젝트별 작업\n"]

    for proj in projects:
        proj_link = f"[[Projects/{proj}|{proj}]]"
        lines.append(f"\n### {proj_link}\n")

        # Work time bounds for this project
        proj_ai = ai_by_proj.get(proj, [])
        proj_files = file_by_proj.get(proj, [])
        all_ts: list[datetime] = []
        for row in proj_ai:
            dt = _to_local(_parse_ts(row.get("timestamp", "")))
            if dt:
                all_ts.append(dt)
        for row in proj_files:
            dt = _to_local(_parse_ts(row.get("timestamp", "")))
            if dt:
                all_ts.append(dt)

        if all_ts:
            start_str = min(all_ts).strftime("%H:%M")
            end_str = max(all_ts).strftime("%H:%M")
            lines.append(f"\n**작업 시간**: {start_str} - {end_str}\n")

        # Changed files
        if proj_files:
            lines.append("\n#### 변경 파일\n")
            for row in proj_files:
                fp = row.get("file_path") or ""
                et = row.get("event_type") or "변경"
                lines.append(f"- `{fp}` ({et})\n")

        # AI sessions
        if proj_ai:
            lines.append("\n#### AI 세션\n")
            # Need to determine global sequence numbers for this date
            for row in proj_ai:
                seq = row.get("_seq", 0)
                tool = _resolve_tool(row)
                prompt = (row.get("prompt_text") or "").replace("\n", " ").strip()
                prompt_preview = prompt[:50] + ("..." if len(prompt) > 50 else "")
                note_id = f"{date_str}-{seq:03d}"
                link = f"[[AI-Sessions/{note_id}|{tool}: {prompt_preview}]]"
                lines.append(f"- {link}\n")

    return "".join(lines)


def _build_frontmatter(
    date_str: str,
    work_start: Optional[str],
    work_end: Optional[str],
    projects: list[str],
    total_ai_sessions: int,
) -> str:
    """Build the YAML frontmatter block."""
    ws = work_start or ""
    we = work_end or ""
    proj_yaml = "[" + ", ".join(projects) + "]" if projects else "[]"
    return (
        f"---\n"
        f"date: {date_str}\n"
        f'work_start: "{ws}"\n'
        f'work_end: "{we}"\n'
        f"tags: [daily]\n"
        f"projects: {proj_yaml}\n"
        f"total_ai_sessions: {total_ai_sessions}\n"
        f"---\n"
    )


def build_daily_note(
    date_str: str,
    ai_rows: list[dict],
    file_rows: list[dict],
) -> str:
    """
    Build the complete Daily Note markdown.

    Assigns sequential numbers to ai_prompt rows (for AI-Sessions links).
    """
    # Assign sequence numbers to AI rows (global order by timestamp)
    for seq, row in enumerate(ai_rows, start=1):
        row["_seq"] = seq

    ai_by_proj = _group_ai_by_project(ai_rows)
    file_by_proj = _group_file_by_project(file_rows)
    projects = _all_projects(ai_by_proj, file_by_proj)
    work_start, work_end = _overall_bounds(ai_rows, file_rows)
    total_ai = len(ai_rows)

    frontmatter = _build_frontmatter(date_str, work_start, work_end, projects, total_ai)
    summary = _build_summary_section(date_str, projects, ai_rows, file_rows)
    timeline = _build_timeline_section(date_str, ai_rows, file_rows)
    projects_section = _build_projects_section(date_str, projects, ai_by_proj, file_by_proj)

    note = (
        frontmatter
        + f"\n# {date_str} 작업 일지\n\n"
        + summary
        + "\n"
        + timeline
        + "\n"
        + projects_section
        + "\n"
    )
    return note


# ---------------------------------------------------------------------------
# Update logic (preserve non-auto sections)
# ---------------------------------------------------------------------------

def create_or_update_daily_note(
    date_str: str,
    db_path: str,
    vault_path: str,
    dry_run: bool = False,
) -> str:
    """
    Create or update the Daily Note for the given date.

    If the note already exists, only "## 타임라인" and "## 프로젝트별 작업"
    sections are replaced; the "## 요약" and any manually edited sections
    are preserved (but frontmatter is re-generated).

    Returns the relative path of the note file.
    """
    relative_path = f"Daily/{date_str}.md"

    ai_rows = _query_ai_prompts(db_path, date_str)
    file_rows = _query_file_events(db_path, date_str)

    # Assign sequence numbers
    for seq, row in enumerate(ai_rows, start=1):
        row["_seq"] = seq

    ai_by_proj = _group_ai_by_project(ai_rows)
    file_by_proj = _group_file_by_project(file_rows)
    projects = _all_projects(ai_by_proj, file_by_proj)
    work_start, work_end = _overall_bounds(ai_rows, file_rows)
    total_ai = len(ai_rows)

    target = Path(vault_path) / relative_path

    if target.exists() and not dry_run:
        # Preserve the existing file; only update auto-generated sections
        print(f"[daily_note] Updating existing note: {relative_path}")
        new_timeline = _build_timeline_section(date_str, ai_rows, file_rows)
        new_projects = _build_projects_section(date_str, projects, ai_by_proj, file_by_proj)

        # Update frontmatter by rebuilding the file header
        existing = target.read_text(encoding="utf-8")
        new_fm = _build_frontmatter(date_str, work_start, work_end, projects, total_ai)

        # Replace frontmatter (between first --- and second ---)
        import re
        fm_pattern = re.compile(r"^---\n.*?^---\n", re.DOTALL | re.MULTILINE)
        if fm_pattern.match(existing):
            existing = fm_pattern.sub(new_fm + "\n", existing, count=1)
        else:
            existing = new_fm + "\n" + existing

        target.write_text(existing, encoding="utf-8")

        # Now update the two auto-generated sections
        update_section(vault_path, relative_path, "## 타임라인", new_timeline)
        update_section(vault_path, relative_path, "## 프로젝트별 작업", new_projects)
        return relative_path

    # Build fresh note
    full_content = build_daily_note(date_str, ai_rows, file_rows)

    if dry_run:
        print(f"\n{'='*60}")
        print(f"[DRY-RUN] Would write: {relative_path}")
        print(f"{'='*60}")
        print(full_content)
        return relative_path

    # Write new file (overwrite=True since we want the fresh build on first create)
    written = write_note(vault_path, relative_path, full_content, overwrite=True)
    if written:
        print(f"[daily_note] Created: {relative_path}")
    else:
        print(f"[daily_note] Already existed, skipped: {relative_path}")

    return relative_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate or update the Obsidian Daily Note."
    )
    parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        default=None,
        help="Date to generate note for (default: today in local time).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print note content to stdout without writing files.",
    )
    args = parser.parse_args()

    date_str = args.date or datetime.now().strftime("%Y-%m-%d")

    try:
        vault_path = _cfg.get_vault_path()
    except RuntimeError as exc:
        print(f"[daily_note] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    db_path = _cfg.get_db_path()

    if not Path(db_path).exists():
        print(
            f"[daily_note] ERROR: Database not found at {db_path}. "
            "Run: python scripts/init_db.py",
            file=sys.stderr,
        )
        sys.exit(1)

    create_or_update_daily_note(
        date_str=date_str,
        db_path=db_path,
        vault_path=vault_path,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
