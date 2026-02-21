"""
scripts/obsidian/project_note.py - Generate/update Project Notes in the Obsidian vault.

Creates or updates {vault}/Projects/{name}.md with Dataview queries showing
recent activity and AI sessions for each project.

Usage:
    python scripts/obsidian/project_note.py [--project NAME] [--dry-run]
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
    from scripts.obsidian.writer import write_note  # type: ignore
except ImportError:
    from obsidian.writer import write_note  # type: ignore


# ---------------------------------------------------------------------------
# DB queries
# ---------------------------------------------------------------------------

def _query_all_projects(db_path: str) -> list[dict]:
    """Return all projects from the projects table."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, name, path, status, created_at FROM projects ORDER BY name"
        ).fetchall()
    return [dict(r) for r in rows]


def _query_projects_from_ai_prompts(db_path: str) -> list[str]:
    """
    Return distinct project names from ai_prompts (for the collector's schema
    where project is stored directly as a text column).
    """
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT project FROM ai_prompts WHERE project IS NOT NULL AND project != ''"
        ).fetchall()
    return [r[0] for r in rows]


def _get_project_first_seen(db_path: str, project_name: str) -> Optional[str]:
    """Return the earliest timestamp seen for a project across all tables."""
    candidates: list[str] = []
    with sqlite3.connect(db_path) as conn:
        # From ai_prompts (text project column)
        row = conn.execute(
            "SELECT MIN(timestamp) FROM ai_prompts WHERE project = ?",
            (project_name,),
        ).fetchone()
        if row and row[0]:
            candidates.append(row[0])

        # From projects table
        row = conn.execute(
            "SELECT created_at FROM projects WHERE name = ?",
            (project_name,),
        ).fetchone()
        if row and row[0]:
            candidates.append(row[0])

    if not candidates:
        return None

    # Pick the earliest
    candidates.sort()
    ts_str = candidates[0].replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(ts_str).astimezone()
        return dt.strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return candidates[0][:10]


def _get_project_path(db_path: str, project_name: str) -> str:
    """Return the filesystem path for a project if stored in the DB."""
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT path FROM projects WHERE name = ?",
            (project_name,),
        ).fetchone()
    return (row[0] or "") if row else ""


# ---------------------------------------------------------------------------
# Note builder
# ---------------------------------------------------------------------------

def build_project_note(
    project_name: str,
    started: Optional[str],
    path: str,
    status: str = "active",
) -> str:
    """
    Build the full markdown content for a Project Note.

    Uses Dataview query blocks for live data in Obsidian.
    """
    started_str = started or datetime.now().strftime("%Y-%m-%d")
    path_str = path or ""

    frontmatter = (
        f"---\n"
        f"type: project\n"
        f"name: {project_name}\n"
        f"status: {status}\n"
        f"started: {started_str}\n"
        f"path: {path_str}\n"
        f"tags: [project]\n"
        f"---\n"
    )

    # Dataview queries - using backtick fences
    recent_activity_query = (
        "```dataview\n"
        "TABLE work_start, total_ai_sessions, file.link AS \"일지\"\n"
        f"FROM \"Daily\"\n"
        f"WHERE contains(projects, \"{project_name}\")\n"
        "SORT date DESC\n"
        "LIMIT 14\n"
        "```"
    )

    ai_sessions_query = (
        "```dataview\n"
        "LIST file.link + \" (\" + tool + \")\"\n"
        f"FROM \"AI-Sessions\"\n"
        f"WHERE project = \"{project_name}\"\n"
        "SORT date DESC\n"
        "```"
    )

    note = (
        frontmatter
        + f"\n# {project_name}\n\n"
        + "## 최근 활동\n\n"
        + recent_activity_query
        + "\n\n"
        + "## AI 세션 목록\n\n"
        + ai_sessions_query
        + "\n"
    )

    return note


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def generate_project_notes(
    db_path: str,
    vault_path: str,
    project_name: Optional[str] = None,
    dry_run: bool = False,
) -> list[str]:
    """
    Generate or update Project Notes.

    If project_name is given, only update that project.
    Otherwise, update all projects found in the DB.

    Returns list of relative paths to written/updated notes.
    """
    # Collect all known project names
    projects_from_table = _query_all_projects(db_path)
    projects_from_ai = _query_projects_from_ai_prompts(db_path)

    # Build a unified set of project names with metadata
    project_map: dict[str, dict] = {}

    for row in projects_from_table:
        name = row.get("name", "").strip()
        if name:
            project_map[name] = {
                "name": name,
                "path": row.get("path") or "",
                "status": row.get("status") or "active",
                "started": None,  # will be computed
            }

    for name in projects_from_ai:
        name = name.strip()
        if name and name not in project_map:
            project_map[name] = {
                "name": name,
                "path": "",
                "status": "active",
                "started": None,
            }

    if not project_map:
        print("[project_note] No projects found in the database.")
        return []

    # Filter if specific project requested
    if project_name:
        pn = project_name.strip()
        if pn not in project_map:
            # Add it even if not in DB
            project_map = {
                pn: {
                    "name": pn,
                    "path": _get_project_path(db_path, pn),
                    "status": "active",
                    "started": None,
                }
            }
        else:
            project_map = {pn: project_map[pn]}

    written_paths: list[str] = []

    for name, meta in sorted(project_map.items()):
        started = _get_project_first_seen(db_path, name)
        path = meta.get("path") or _get_project_path(db_path, name)
        status = meta.get("status") or "active"

        relative_path = f"Projects/{name}.md"
        content = build_project_note(name, started, path, status)

        if dry_run:
            print(f"\n{'='*60}")
            print(f"[DRY-RUN] Would write: {relative_path}")
            print(f"{'='*60}")
            print(content)
            written_paths.append(relative_path)
        else:
            # Always overwrite project notes (they contain Dataview queries, not manual content)
            written = write_note(vault_path, relative_path, content, overwrite=True)
            if written:
                print(f"[project_note] Written: {relative_path}")
            else:
                print(f"[project_note] Written: {relative_path}")
            written_paths.append(relative_path)

    return written_paths


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate or update Project Notes in the Obsidian vault."
    )
    parser.add_argument(
        "--project",
        metavar="NAME",
        default=None,
        help="Project name to generate note for (default: all projects).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print note content to stdout without writing files.",
    )
    args = parser.parse_args()

    try:
        vault_path = _cfg.get_vault_path()
    except RuntimeError as exc:
        print(f"[project_note] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    db_path = _cfg.get_db_path()

    if not Path(db_path).exists():
        print(
            f"[project_note] ERROR: Database not found at {db_path}. "
            "Run: python scripts/init_db.py",
            file=sys.stderr,
        )
        sys.exit(1)

    generate_project_notes(
        db_path=db_path,
        vault_path=vault_path,
        project_name=args.project,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
