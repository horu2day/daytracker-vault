"""
scripts/processors/project_mapper.py - Map file paths to project names.

A "project" is the immediate subdirectory under one of the configured
watch_roots.  For example, if watch_roots = ["C:/MYCLAUDE_PROJECT"] and
the file path is:

    C:/MYCLAUDE_PROJECT/daytracker-vault/scripts/config.py

then the project name is "daytracker-vault" (the first component below
the watch root).

The project is automatically registered in the `projects` table of
data/worklog.db if it does not already exist.

Usage:
    python -m scripts.processors.project_mapper --path "C:/MYCLAUDE_PROJECT/daytracker-vault/scripts/config.py"
    python scripts/processors/project_mapper.py --path "..."
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath


# ---------------------------------------------------------------------------
# Path normalisation helper
# ---------------------------------------------------------------------------

def _normalise(p: str | Path) -> Path:
    """
    Return a resolved Path object, handling both Windows and POSIX paths.
    On Windows, Path() handles backslashes natively.
    On POSIX hosts running Windows paths (e.g. MSYS2 / git-bash), we do a
    best-effort conversion.
    """
    p_str = str(p).replace("\\", "/")
    resolved = Path(p_str)
    # Don't call resolve() on paths that don't exist (they may be hypothetical)
    return resolved


# ---------------------------------------------------------------------------
# Core mapping function
# ---------------------------------------------------------------------------

def map_path_to_project(file_path: str, watch_roots: list[str] | None = None) -> str | None:
    """
    Given a file path, return the project name (immediate subdir of watch root).

    Parameters
    ----------
    file_path:
        Absolute path to a file or directory.
    watch_roots:
        List of root directories to check.  If None, loads from config.

    Returns
    -------
    str | None
        The project name, or None if the path doesn't fall under any watch root.

    Example
    -------
    >>> map_path_to_project("C:/MYCLAUDE_PROJECT/daytracker-vault/scripts/foo.py")
    'daytracker-vault'
    """
    if watch_roots is None:
        watch_roots = _get_watch_roots()

    file_p = _normalise(file_path)

    for root_str in watch_roots:
        root_p = _normalise(root_str)

        # Check if file_p is relative to root_p
        try:
            rel = file_p.relative_to(root_p)
        except ValueError:
            # Not under this root; try case-insensitive comparison on Windows
            try:
                file_lower = Path(str(file_p).lower())
                root_lower = Path(str(root_p).lower())
                rel = file_lower.relative_to(root_lower)
            except ValueError:
                continue

        parts = rel.parts
        if parts:
            return parts[0]  # first component = project name

    return None


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_or_create_project(name: str, path: str = "", db_path: str | None = None) -> int:
    """
    Return the project ID from the `projects` table, inserting a new row if
    the project doesn't exist yet.

    Parameters
    ----------
    name:
        Project name (unique key).
    path:
        Optional filesystem path to associate with the project.
    db_path:
        Path to the SQLite database.  If None, uses data/worklog.db.

    Returns
    -------
    int
        The project row ID.
    """
    if db_path is None:
        project_root = Path(__file__).resolve().parent.parent.parent
        db_path = str(project_root / "data" / "worklog.db")

    db = Path(db_path)
    if not db.exists():
        raise FileNotFoundError(
            f"Database not found at {db_path}. "
            "Run: python scripts/init_db.py"
        )

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")

        # Try to fetch existing
        row = conn.execute(
            "SELECT id FROM projects WHERE name = ?", (name,)
        ).fetchone()

        if row:
            return int(row[0])

        # Insert new project
        now = datetime.now(tz=timezone.utc).isoformat()
        cursor = conn.execute(
            "INSERT INTO projects (name, path, status, created_at) VALUES (?, ?, 'active', ?)",
            (name, path, now),
        )
        conn.commit()
        project_id = cursor.lastrowid
        print(f"[project_mapper] New project registered: '{name}' (id={project_id})")
        return int(project_id)


# ---------------------------------------------------------------------------
# Config helper (lazy import to avoid circular deps)
# ---------------------------------------------------------------------------

def _get_watch_roots() -> list[str]:
    """Load watch_roots from config, with graceful fallback."""
    try:
        project_root = Path(__file__).resolve().parent.parent.parent
        sys.path.insert(0, str(project_root))
        from scripts.config import Config  # type: ignore
        cfg = Config(project_root=project_root)
        return cfg.watch_roots
    except Exception as exc:  # noqa: BLE001
        print(
            f"[project_mapper] WARNING: Could not load config: {exc}",
            file=sys.stderr,
        )
        return []


def _get_db_path() -> str:
    """Return the default DB path."""
    try:
        project_root = Path(__file__).resolve().parent.parent.parent
        sys.path.insert(0, str(project_root))
        from scripts.config import Config  # type: ignore
        cfg = Config(project_root=project_root)
        return cfg.get_db_path()
    except Exception:  # noqa: BLE001
        project_root = Path(__file__).resolve().parent.parent.parent
        return str(project_root / "data" / "worklog.db")


# ---------------------------------------------------------------------------
# High-level convenience
# ---------------------------------------------------------------------------

def resolve_project_id(file_path: str) -> int | None:
    """
    Convenience: map a file path to its project name and return the DB project
    ID, creating a new projects row if needed.

    Returns None if the path doesn't fall under any watch root or on error.
    """
    project_name = map_path_to_project(file_path)
    if not project_name:
        return None

    # Determine project root path (the watch_root/project_name directory)
    watch_roots = _get_watch_roots()
    file_p = _normalise(file_path)
    project_path = ""
    for root_str in watch_roots:
        root_p = _normalise(root_str)
        try:
            rel = file_p.relative_to(root_p)
            project_path = str(root_p / rel.parts[0])
            break
        except ValueError:
            continue

    try:
        db_path = _get_db_path()
        return get_or_create_project(project_name, project_path, db_path)
    except FileNotFoundError:
        print(
            "[project_mapper] WARNING: Database does not exist. "
            "Run: python scripts/init_db.py",
            file=sys.stderr,
        )
        return None
    except Exception as exc:  # noqa: BLE001
        print(
            f"[project_mapper] ERROR: Could not get/create project: {exc}",
            file=sys.stderr,
        )
        return None


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Map a file path to its DayTracker project name.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            '  python -m scripts.processors.project_mapper --path "C:/MYCLAUDE_PROJECT/daytracker-vault/scripts/config.py"\n'
            '  python scripts/processors/project_mapper.py --path "C:/work/my-app/src/main.py"'
        ),
    )
    parser.add_argument(
        "--path",
        required=True,
        help="File or directory path to map to a project name.",
    )
    parser.add_argument(
        "--register",
        action="store_true",
        default=True,
        help="Register the project in the database if not already present (default: True).",
    )
    parser.add_argument(
        "--no-register",
        action="store_false",
        dest="register",
        help="Only print the project name; do not touch the database.",
    )
    args = parser.parse_args()

    project_name = map_path_to_project(args.path)

    if project_name is None:
        print(
            f"[project_mapper] '{args.path}' does not fall under any watch_root.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"[project_mapper] project: {project_name}")

    if args.register:
        project_id = resolve_project_id(args.path)
        if project_id is not None:
            print(f"[project_mapper] project_id: {project_id}")
        else:
            print(
                "[project_mapper] Could not register project in DB "
                "(DB may not be initialised).",
                file=sys.stderr,
            )


if __name__ == "__main__":
    main()
