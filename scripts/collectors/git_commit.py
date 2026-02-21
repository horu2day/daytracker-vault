"""
scripts/collectors/git_commit.py - Git post-commit event recorder.

Called by the git post-commit hook after each commit. Reads the latest
commit metadata via git commands, maps the repo to a project, and inserts
one row into activity_log (event_type='git_commit') plus one row per
changed file into file_events.

Designed to run silently in the background (no stdout in normal operation).
Errors are written to stderr (which git captures but does not display).

Usage:
    python scripts/collectors/git_commit.py --repo /path/to/repo
    python scripts/collectors/git_commit.py --repo "C:/MYCLAUDE_PROJECT/daytracker-vault"
"""

from __future__ import annotations

import io
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# UTF-8 stdout/stderr on Windows
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    if hasattr(sys.stdout, "buffer") and not getattr(sys.stdout, "_daytracker_wrapped", False):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stdout._daytracker_wrapped = True  # type: ignore[attr-defined]
    if hasattr(sys.stderr, "buffer") and not getattr(sys.stderr, "_daytracker_wrapped", False):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
        sys.stderr._daytracker_wrapped = True  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.config import Config  # noqa: E402
from scripts.processors.project_mapper import (  # noqa: E402
    map_path_to_project,
    get_or_create_project,
)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _run_git(args: list[str], cwd: str) -> str:
    """
    Run a git command in the specified directory and return its stdout.

    Parameters
    ----------
    args:
        Git subcommand and arguments (without the leading "git").
    cwd:
        Working directory (the repo root).

    Returns
    -------
    str
        Stripped stdout output.

    Raises
    ------
    RuntimeError
        If git returns a non-zero exit code.
    """
    cmd = ["git"] + args
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(args)} failed (rc={result.returncode}): {result.stderr.strip()}"
            )
        return result.stdout.strip()
    except FileNotFoundError:
        raise RuntimeError("git executable not found on PATH") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"git {' '.join(args)} timed out after 15s") from None


def get_latest_commit(repo_path: str) -> dict:
    """
    Return metadata for the most recent commit in the given repository.

    Parameters
    ----------
    repo_path:
        Absolute path to the repository root.

    Returns
    -------
    dict with keys:
        hash        (str)  - full commit hash
        short_hash  (str)  - 7-char abbreviated hash
        subject     (str)  - commit message subject line
        author      (str)  - author name
        timestamp   (str)  - ISO 8601 date string (author date with timezone)
    """
    sep = "|DTSP|"
    fmt = f"%H{sep}%h{sep}%s{sep}%an{sep}%aI"
    raw = _run_git(["log", "-1", f"--pretty=format:{fmt}", "HEAD"], repo_path)

    parts = raw.split(sep)
    if len(parts) != 5:
        raise RuntimeError(f"Unexpected git log output: {raw!r}")

    return {
        "hash": parts[0],
        "short_hash": parts[1],
        "subject": parts[2],
        "author": parts[3],
        "timestamp": parts[4],
    }


def get_changed_files(repo_path: str) -> list[str]:
    """
    Return a list of file paths changed by the latest commit (HEAD).

    Parameters
    ----------
    repo_path:
        Absolute path to the repository root.

    Returns
    -------
    list[str]
        Relative file paths (relative to repo root).
    """
    raw = _run_git(
        ["diff-tree", "--no-commit-id", "-r", "--name-only", "HEAD"],
        repo_path,
    )
    if not raw:
        return []
    return [line for line in raw.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Database insertion
# ---------------------------------------------------------------------------

def record_commit(
    repo_path: str,
    commit: dict,
    changed_files: list[str],
    db_path: str,
    config: Optional[Config] = None,
    dry_run: bool = False,
) -> None:
    """
    Insert commit data into activity_log and file_events tables.

    Parameters
    ----------
    repo_path:
        Absolute path to the repository root.
    commit:
        Dict returned by get_latest_commit().
    changed_files:
        List of relative file paths returned by get_changed_files().
    db_path:
        Absolute path to worklog.db.
    config:
        Optional Config instance.
    dry_run:
        If True, print to stdout and do not touch the database.
    """
    # Resolve project
    watch_roots = config.watch_roots if config else []
    project_name = map_path_to_project(repo_path, watch_roots)
    project_id: Optional[int] = None

    if project_name:
        if not dry_run:
            try:
                project_id = get_or_create_project(project_name, repo_path, db_path)
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[git_commit] WARNING: Could not resolve project '{project_name}': {exc}",
                    file=sys.stderr,
                )
    else:
        project_name = Path(repo_path).name  # fallback: repo folder name

    timestamp = commit.get("timestamp", datetime.now(tz=timezone.utc).isoformat())
    short_hash = commit.get("short_hash", "?")
    subject = commit.get("subject", "")
    author = commit.get("author", "")
    full_hash = commit.get("hash", "")

    summary = f"git commit [{short_hash}] {subject}"
    data_blob = json.dumps(
        {
            "source": "git_commit",
            "repo": repo_path,
            "hash": full_hash,
            "short_hash": short_hash,
            "subject": subject,
            "author": author,
            "changed_files": changed_files,
        },
        ensure_ascii=False,
    )

    if dry_run:
        print(f"[git_commit][dry-run] Repo:    {repo_path}")
        print(f"[git_commit][dry-run] Project: {project_name} (id={project_id})")
        print(f"[git_commit][dry-run] Commit:  {short_hash} by {author} at {timestamp}")
        print(f"[git_commit][dry-run] Message: {subject}")
        print(f"[git_commit][dry-run] Files ({len(changed_files)}):")
        for f in changed_files:
            print(f"  - {f}")
        print("[git_commit][dry-run] (no DB writes performed)")
        return

    if not Path(db_path).exists():
        print(
            f"[git_commit] WARNING: DB not found at {db_path}. "
            "Run: python scripts/init_db.py",
            file=sys.stderr,
        )
        return

    try:
        with sqlite3.connect(db_path, timeout=10) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")

            # Insert activity_log row
            cursor = conn.execute(
                """
                INSERT INTO activity_log
                    (timestamp, event_type, project_id, app_name, summary, data)
                VALUES (?, 'git_commit', ?, 'git', ?, ?)
                """,
                (timestamp, project_id, summary, data_blob),
            )
            activity_id = cursor.lastrowid

            # Insert file_events rows
            for rel_path in changed_files:
                abs_path = str(Path(repo_path) / rel_path)
                file_size: Optional[int] = None
                try:
                    p = Path(abs_path)
                    if p.exists():
                        file_size = p.stat().st_size
                except OSError:
                    pass

                conn.execute(
                    """
                    INSERT INTO file_events
                        (activity_id, timestamp, file_path, event_type, project_id, file_size)
                    VALUES (?, ?, ?, 'modified', ?, ?)
                    """,
                    (activity_id, timestamp, abs_path, project_id, file_size),
                )

            conn.commit()

        # Silent success (background hook - no stdout)

    except sqlite3.Error as exc:
        print(
            f"[git_commit] ERROR: Database error: {exc}",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# Main run function
# ---------------------------------------------------------------------------

def run(
    repo_path: str,
    dry_run: bool = False,
    config: Optional[Config] = None,
) -> None:
    """
    Main entry point: read latest git commit and record it to the DB.

    Parameters
    ----------
    repo_path:
        Absolute path to the git repository.
    dry_run:
        If True, print results without writing to the database.
    config:
        Optional Config instance.
    """
    if config is None:
        config = Config(project_root=PROJECT_ROOT)

    repo_path = str(Path(repo_path).resolve())

    # Validate that this is a git repo
    git_dir = Path(repo_path) / ".git"
    if not git_dir.exists():
        print(
            f"[git_commit] ERROR: Not a git repository (no .git found): {repo_path}",
            file=sys.stderr,
        )
        return

    # Get commit info
    try:
        commit = get_latest_commit(repo_path)
    except RuntimeError as exc:
        print(f"[git_commit] ERROR: Could not read commit: {exc}", file=sys.stderr)
        return

    # Get changed files
    try:
        changed_files = get_changed_files(repo_path)
    except RuntimeError as exc:
        print(
            f"[git_commit] WARNING: Could not list changed files: {exc}",
            file=sys.stderr,
        )
        changed_files = []

    db_path = config.get_db_path()
    record_commit(
        repo_path=repo_path,
        commit=commit,
        changed_files=changed_files,
        db_path=db_path,
        config=config,
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "DayTracker: record the latest git commit to the database. "
            "Designed to be called from a git post-commit hook."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/collectors/git_commit.py --repo /path/to/repo\n"
            '  python scripts/collectors/git_commit.py --repo "C:/MYCLAUDE_PROJECT/daytracker-vault"\n'
            "  python scripts/collectors/git_commit.py --repo . --dry-run"
        ),
    )
    parser.add_argument(
        "--repo",
        required=True,
        help="Absolute path to the git repository root.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commit info to stdout; do not write to the database.",
    )
    args = parser.parse_args()

    try:
        run(repo_path=args.repo, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001
        print(f"[git_commit] FATAL: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
