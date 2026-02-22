"""
scripts/agents/context_agent.py - DayTracker Context Recovery Agent.

Displays the recent work context for a specific project:
  1. Last active date for the project
  2. Recent AI sessions (last 10, showing timestamp + prompt preview)
  3. Recently modified files (last 10)
  4. Recent git commits (git log --oneline -5)
  5. A suggested "pick up where you left off" hint

Usage:
    python scripts/agents/context_agent.py [--project PROJECT] [--dry-run]
    python -m scripts.agents.context_agent [--project PROJECT] [--dry-run]

If --project is not specified, the agent auto-detects the project from the
current working directory using the same watch_roots logic as project_mapper.
"""

from __future__ import annotations

import argparse
import io
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
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


def _fmt_local(ts_str: str, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Convert UTC timestamp string to local time formatted string."""
    dt = _to_local(_parse_ts(ts_str))
    if dt is None:
        return ts_str
    return dt.strftime(fmt)


# ---------------------------------------------------------------------------
# Project detection
# ---------------------------------------------------------------------------

def detect_project(project_arg: Optional[str] = None) -> tuple[str, str]:
    """
    Detect the current project name and its filesystem path.

    Parameters
    ----------
    project_arg : str | None
        If provided, look up this project name in the DB to get its path.
        If None, auto-detect from the current working directory.

    Returns
    -------
    tuple[str, str]
        (project_name, project_path)
        project_path may be empty string if not found in DB.

    Raises
    ------
    SystemExit
        If no project can be determined.
    """
    cfg = _cfg
    if cfg is None:
        print("[context_agent] ERROR: Could not load config.", file=sys.stderr)
        sys.exit(1)

    if project_arg:
        # Look up path from DB
        db_path = cfg.get_db_path()
        project_path = _get_project_path_from_db(db_path, project_arg)
        return project_arg, (project_path or "")

    # Auto-detect from cwd
    cwd = Path.cwd()
    try:
        from scripts.processors.project_mapper import map_path_to_project  # type: ignore
    except ImportError:
        try:
            from processors.project_mapper import map_path_to_project  # type: ignore
        except ImportError:
            print(
                "[context_agent] ERROR: Could not import project_mapper.",
                file=sys.stderr,
            )
            sys.exit(1)

    watch_roots = cfg.watch_roots
    project_name = map_path_to_project(str(cwd), watch_roots)

    if not project_name:
        # Try parent directories
        for parent in cwd.parents:
            project_name = map_path_to_project(str(parent), watch_roots)
            if project_name:
                cwd = parent
                break

    if not project_name:
        print(
            f"[context_agent] ERROR: Could not detect project from cwd: {cwd}\n"
            "  Use --project PROJECT to specify one explicitly.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Determine project root path
    project_path = ""
    for root_str in watch_roots:
        root_p = Path(root_str.replace("\\", "/"))
        try:
            cwd_p = Path(str(cwd).replace("\\", "/"))
            rel = cwd_p.relative_to(root_p)
            project_path = str(root_p / rel.parts[0]) if rel.parts else str(cwd_p)
            break
        except ValueError:
            # Try case-insensitive
            try:
                rel = Path(str(cwd_p).lower()).relative_to(Path(str(root_p).lower()))
                project_path = str(root_p / rel.parts[0]) if rel.parts else str(cwd_p)
                break
            except ValueError:
                continue

    return project_name, project_path


def _get_project_path_from_db(db_path: str, project_name: str) -> Optional[str]:
    """Look up project path in the DB by name."""
    if not Path(db_path).exists():
        return None
    try:
        with sqlite3.connect(db_path, timeout=10) as conn:
            row = conn.execute(
                "SELECT path FROM projects WHERE name = ?", (project_name,)
            ).fetchone()
        return row[0] if row else None
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# DB queries
# ---------------------------------------------------------------------------

def get_project_history(db_path: str, project_name: str, limit: int = 10) -> dict:
    """
    Query all relevant history for a project.

    Returns a dict with keys:
        project_name   - str
        last_active    - str (YYYY-MM-DD, local) or None
        ai_sessions    - list of dicts {timestamp_local, tool, prompt_preview}
        file_events    - list of dicts {timestamp_local, file_path, event_type}
    """
    result: dict = {
        "project_name": project_name,
        "last_active": None,
        "ai_sessions": [],
        "file_events": [],
    }

    if not Path(db_path).exists():
        return result

    try:
        with sqlite3.connect(db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row

            # Get project_id
            proj_row = conn.execute(
                "SELECT id FROM projects WHERE name = ?", (project_name,)
            ).fetchone()

            proj_id: Optional[int] = proj_row["id"] if proj_row else None

            # AI sessions: match by project_id OR project column name
            if proj_id is not None:
                ai_rows = conn.execute(
                    """
                    SELECT timestamp, tool, prompt_text, session_id
                    FROM ai_prompts
                    WHERE project_id = ? OR project = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (proj_id, project_name, limit),
                ).fetchall()
            else:
                ai_rows = conn.execute(
                    """
                    SELECT timestamp, tool, prompt_text, session_id
                    FROM ai_prompts
                    WHERE project = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (project_name, limit),
                ).fetchall()

            sessions = []
            for r in ai_rows:
                prompt = (r["prompt_text"] or "").replace("\n", " ").strip()
                preview = prompt[:80] + ("..." if len(prompt) > 80 else "")
                sessions.append({
                    "timestamp_local": _fmt_local(r["timestamp"]),
                    "tool": r["tool"] or "claude-code",
                    "prompt_preview": preview,
                })
            result["ai_sessions"] = sessions

            # File events: match by project_id
            if proj_id is not None:
                fe_rows = conn.execute(
                    """
                    SELECT timestamp, file_path, event_type
                    FROM file_events
                    WHERE project_id = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (proj_id, limit),
                ).fetchall()
            else:
                fe_rows = []

            file_events = []
            for r in fe_rows:
                file_events.append({
                    "timestamp_local": _fmt_local(r["timestamp"]),
                    "file_path": r["file_path"] or "",
                    "event_type": r["event_type"] or "modified",
                })
            result["file_events"] = file_events

            # Last active date: MAX of all timestamps for this project
            all_ts: list[str] = []
            for r in ai_rows:
                if r["timestamp"]:
                    all_ts.append(r["timestamp"])
            for r in fe_rows:
                if r["timestamp"]:
                    all_ts.append(r["timestamp"])

            if all_ts:
                latest_utc = max(all_ts)
                dt = _to_local(_parse_ts(latest_utc))
                if dt:
                    today = datetime.now().date()
                    last_date = dt.date()
                    diff = (today - last_date).days
                    if diff == 0:
                        result["last_active"] = f"{dt.strftime('%Y-%m-%d')} (today)"
                    elif diff == 1:
                        result["last_active"] = f"{dt.strftime('%Y-%m-%d')} (yesterday)"
                    else:
                        result["last_active"] = f"{dt.strftime('%Y-%m-%d')} ({diff} days ago)"

    except Exception as exc:  # noqa: BLE001
        print(f"[context_agent] WARNING: DB query failed: {exc}", file=sys.stderr)

    return result


# ---------------------------------------------------------------------------
# Git log
# ---------------------------------------------------------------------------

def get_git_log(project_path: str, n: int = 5) -> list[str]:
    """
    Run `git log --oneline -N` on the project path.

    Returns a list of commit line strings, or [] on error.
    """
    if not project_path or not Path(project_path).exists():
        return []

    try:
        result = subprocess.run(
            ["git", "log", "--oneline", f"-{n}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=project_path,
            timeout=10,
        )
        if result.returncode == 0:
            return [line for line in result.stdout.splitlines() if line.strip()]
        return []
    except Exception as exc:  # noqa: BLE001
        # git might not be installed, or not a git repo
        print(f"[context_agent] WARNING: git log failed: {exc}", file=sys.stderr)
        return []


# ---------------------------------------------------------------------------
# Context summary generator
# ---------------------------------------------------------------------------

def generate_context(data: dict) -> str:
    """
    Generate the context summary as a plain text string.

    Parameters
    ----------
    data : dict
        Expected keys:
            project_name, project_path, last_active,
            ai_sessions, file_events, git_commits
    """
    project_name: str = data.get("project_name", "unknown")
    project_path: str = data.get("project_path", "")
    last_active: Optional[str] = data.get("last_active")
    ai_sessions: list[dict] = data.get("ai_sessions", [])
    file_events: list[dict] = data.get("file_events", [])
    git_commits: list[str] = data.get("git_commits", [])

    lines: list[str] = []

    # ── Header ───────────────────────────────────────────────────────────────
    title = f"  Context: {project_name}  "
    width = max(len(title) + 4, 54)
    border = "=" * (width - 2)
    lines.append(f"+{border}+")
    lines.append(f"|{title.ljust(width - 2)}|")
    lines.append(f"+{border}+")
    lines.append("")

    if project_path:
        lines.append(f"Path: {project_path}")
        lines.append("")

    # ── Last active ──────────────────────────────────────────────────────────
    lines.append(f"[Last Active] {last_active or '(no records found)'}")
    lines.append("")

    # ── AI sessions ─────────────────────────────────────────────────────────
    n_ai = len(ai_sessions)
    lines.append(f"[AI Sessions] Recent {n_ai} session(s)")
    if ai_sessions:
        for sess in ai_sessions:
            ts = sess["timestamp_local"]
            tool = sess["tool"]
            preview = sess["prompt_preview"]
            lines.append(f"  * {ts}  {tool}: {preview}")
    else:
        lines.append("  (no AI sessions recorded for this project)")
    lines.append("")

    # ── File events ──────────────────────────────────────────────────────────
    n_files = len(file_events)
    lines.append(f"[Recent Files] Last {n_files} modified file(s)")
    if file_events:
        for fe in file_events:
            ts = fe["timestamp_local"]
            fp = fe["file_path"]
            # Display just the filename for brevity, full path on next line
            fp_name = Path(fp).name if fp else "?"
            lines.append(f"  * {fp_name}  ({ts})")
            if fp and fp_name != fp:
                lines.append(f"    {fp}")
    else:
        lines.append("  (no file events recorded for this project)")
    lines.append("")

    # ── Git commits ──────────────────────────────────────────────────────────
    lines.append(f"[Git Commits] Recent {len(git_commits)} commit(s)")
    if git_commits:
        for commit_line in git_commits:
            lines.append(f"  * {commit_line}")
    else:
        lines.append("  (no git log available)")
    lines.append("")

    # ── Suggested action ─────────────────────────────────────────────────────
    lines.append("[Suggested] Pick up where you left off")
    if ai_sessions:
        last_ai = ai_sessions[0]
        preview = last_ai["prompt_preview"]
        short = preview[:60] + ("..." if len(preview) > 60 else "")
        lines.append(f"  Last AI session: \"{short}\"")
        if git_commits:
            lines.append(f"  Last commit: {git_commits[0]}")
        lines.append("  -> Continue from this point?")
    elif file_events:
        last_fe = file_events[0]
        fp_name = Path(last_fe["file_path"]).name
        lines.append(f"  Last file modified: {fp_name} ({last_fe['timestamp_local']})")
        lines.append("  -> Continue from this point?")
    else:
        lines.append("  (no previous context found; start fresh)")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main run function
# ---------------------------------------------------------------------------

def run(project: Optional[str] = None, dry_run: bool = False) -> None:
    """
    Entry point for the context agent.

    Parameters
    ----------
    project : str | None
        Project name to show context for. Auto-detected from cwd if None.
    dry_run : bool
        If True, only print to stdout; do not write any files.
    """
    cfg = _cfg
    if cfg is None:
        print("[context_agent] ERROR: Could not load config.", file=sys.stderr)
        sys.exit(1)

    db_path = cfg.get_db_path()

    # Detect project
    project_name, project_path = detect_project(project)

    # Query DB history
    history = get_project_history(db_path, project_name, limit=10)

    # Git log
    git_commits = get_git_log(project_path, n=5)

    # Assemble data dict
    data = {
        "project_name": project_name,
        "project_path": project_path,
        "last_active": history["last_active"],
        "ai_sessions": history["ai_sessions"],
        "file_events": history["file_events"],
        "git_commits": git_commits,
    }

    # Generate and print context
    context_text = generate_context(data)
    print(context_text)

    if dry_run:
        print("[context_agent] DRY-RUN: no files written.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "DayTracker Context Agent - show recent history for a project "
            "to quickly restore working context."
        ),
    )
    parser.add_argument(
        "--project",
        metavar="PROJECT",
        default=None,
        help=(
            "Project name to show context for. "
            "If not specified, auto-detected from the current working directory."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print context to stdout only; do not write any files.",
    )
    args = parser.parse_args()
    run(project=args.project, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
