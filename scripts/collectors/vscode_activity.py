"""
scripts/collectors/vscode_activity.py - VSCode log reader for project activity.

Fallback collector when Wakapi is not available. Reads VSCode's own log files
to detect recently active workspaces/folders. This is a best-effort approach:
log format may change across VSCode versions and data extraction is heuristic.

VSCode log location:
  Windows : %APPDATA%/Code/logs/
  Mac/Linux: ~/Library/Application Support/Code/logs/ (Mac)
             ~/.config/Code/logs/ (Linux)

The logs contain JSON entries referencing recently opened workspaces.
We look for lines/files containing "workspaceFolder", "openedPathsList",
"openRecent", or folder paths under known watch_roots.

Usage:
    python scripts/collectors/vscode_activity.py [--dry-run] [--hours N]
    python -m scripts.collectors.vscode_activity [--dry-run] [--hours N]
"""

from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# UTF-8 stdout on Windows (guard against double-wrapping when imported)
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
# VSCode log directory detection
# ---------------------------------------------------------------------------

def get_vscode_log_dir() -> Optional[Path]:
    """
    Return the VSCode logs directory for the current platform.

    Returns None if the directory cannot be found.

    Locations:
      Windows  : %APPDATA%/Code/logs
      macOS    : ~/Library/Application Support/Code/logs
      Linux    : ~/.config/Code/logs
    """
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            candidate = Path(appdata) / "Code" / "logs"
            if candidate.exists():
                return candidate
        # Try home-relative fallback
        candidate = Path.home() / "AppData" / "Roaming" / "Code" / "logs"
        if candidate.exists():
            return candidate

    elif sys.platform == "darwin":
        candidate = Path.home() / "Library" / "Application Support" / "Code" / "logs"
        if candidate.exists():
            return candidate

    else:
        # Linux
        candidate = Path.home() / ".config" / "Code" / "logs"
        if candidate.exists():
            return candidate
        # XDG_CONFIG_HOME override
        xdg = os.environ.get("XDG_CONFIG_HOME", "")
        if xdg:
            candidate = Path(xdg) / "Code" / "logs"
            if candidate.exists():
                return candidate

    return None


# ---------------------------------------------------------------------------
# Regex patterns for workspace extraction
# ---------------------------------------------------------------------------

# Matches VSCode workspace folder URIs like:
#   "uri":"file:///C:/Users/foo/work/my-project"
#   "workspaceFolder":{"uri":"file:///home/user/work/project"}
#   "openedPathsList":{"workspaces3":[...]}
_URI_PATTERNS = [
    re.compile(r'"(?:uri|path|folder|workspace|workspaceFolder|fsPath)"\s*:\s*"file://(/[^"]+)"', re.IGNORECASE),
    re.compile(r'"(?:uri|path|folder|workspace|workspaceFolder|fsPath)"\s*:\s*"file:///([A-Za-z]:[^"]+)"', re.IGNORECASE),
    re.compile(r'file:///([A-Za-z]:[^\s"\']+)', re.IGNORECASE),
    re.compile(r'file://(/(?:home|Users)/[^\s"\']+)', re.IGNORECASE),
]

# Fallback: bare absolute paths that look like project folders
_PATH_PATTERN_WIN = re.compile(r'"([A-Za-z]:\\[^"\\<>|?\*]+)"')
_PATH_PATTERN_POSIX = re.compile(r'"/(?:home|Users)/[^"]+(?:/[^"]+){1,5}"')


def _extract_paths_from_line(line: str) -> list[str]:
    """
    Extract potential workspace paths from a single log line.

    Returns a list of normalised absolute path strings.
    Deduplication is done by the caller.
    """
    found: list[str] = []

    for pattern in _URI_PATTERNS:
        for m in pattern.finditer(line):
            raw = m.group(1)
            # URL-decode %20 etc.
            raw = raw.replace("%20", " ").replace("%3A", ":").replace("%2F", "/")
            # Remove leading slash on Windows paths (file:///C:/... -> C:/...)
            if raw.startswith("/") and len(raw) > 2 and raw[2] == ":":
                raw = raw[1:]
            raw = raw.replace("\\", "/").rstrip("/")
            if raw:
                found.append(raw)

    return found


def _is_likely_project_dir(path_str: str, watch_roots: list[str]) -> bool:
    """
    Return True if path_str looks like a real project directory.
    - Must exist on disk (or at least its parent does)
    - Must be under one of the watch_roots (or be plausible regardless)
    """
    p = Path(path_str.replace("\\", "/"))
    # Must have reasonable depth (not root itself)
    if len(p.parts) < 2:
        return False
    # If under watch_roots, accept even if not on disk (repo may be on a different machine)
    for root_str in watch_roots:
        root = Path(root_str.replace("\\", "/"))
        try:
            p.relative_to(root)
            return True
        except ValueError:
            continue
    # Otherwise require the path to exist
    return p.exists()


# ---------------------------------------------------------------------------
# Log file scanner
# ---------------------------------------------------------------------------

def scan_log_files(
    log_dir: Path,
    since: datetime,
    watch_roots: list[str],
) -> list[dict]:
    """
    Scan VSCode log files modified after `since` and extract workspace paths.

    Parameters
    ----------
    log_dir:
        VSCode logs directory.
    since:
        Only read log files modified after this timestamp (UTC).
    watch_roots:
        Used to filter and annotate paths.

    Returns
    -------
    list[dict]
        Each dict has keys: path (str), project (str | None), mtime (datetime)
    """
    results: list[dict] = []
    seen_paths: set[str] = set()

    # Walk log_dir recursively; VSCode logs are in dated sub-directories
    try:
        all_log_files: list[tuple[datetime, Path]] = []
        for root, _dirs, files in os.walk(log_dir):
            for fname in files:
                if not (fname.endswith(".log") or fname.endswith(".json")):
                    continue
                fpath = Path(root) / fname
                try:
                    mtime_ts = fpath.stat().st_mtime
                    mtime = datetime.fromtimestamp(mtime_ts, tz=timezone.utc)
                except OSError:
                    continue
                if mtime >= since:
                    all_log_files.append((mtime, fpath))
    except PermissionError as exc:
        print(
            f"[vscode_activity] WARNING: Cannot scan log dir {log_dir}: {exc}",
            file=sys.stderr,
        )
        return results

    # Sort by modification time descending (newest first) and limit to 50 files
    all_log_files.sort(key=lambda t: t[0], reverse=True)
    all_log_files = all_log_files[:50]

    for mtime, fpath in all_log_files:
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for line in content.splitlines():
            # Quick pre-filter: skip lines without path indicators
            if "file://" not in line and "workspaceFolder" not in line:
                continue

            extracted = _extract_paths_from_line(line)
            for raw_path in extracted:
                norm = raw_path.replace("\\", "/")
                if norm in seen_paths:
                    continue

                if not _is_likely_project_dir(norm, watch_roots):
                    continue

                seen_paths.add(norm)
                project = map_path_to_project(norm, watch_roots)
                results.append({
                    "path": norm,
                    "project": project,
                    "mtime": mtime,
                })

    return results


# ---------------------------------------------------------------------------
# Database insertion
# ---------------------------------------------------------------------------

def sync_to_db(
    activities: list[dict],
    db_path: str,
    config: Optional[Config] = None,
) -> int:
    """
    Insert detected VSCode workspace activity into activity_log.

    Deduplicates: does not insert a row if one already exists for the same
    project + date + event_type='vscode_activity'.

    Parameters
    ----------
    activities:
        List of dicts from scan_log_files().
    db_path:
        Absolute path to worklog.db.
    config:
        Optional Config instance.

    Returns
    -------
    int
        Number of new rows inserted.
    """
    if not activities:
        return 0

    watch_roots = config.watch_roots if config else []
    inserted = 0

    try:
        with sqlite3.connect(db_path, timeout=10) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")

            for act in activities:
                proj_name = act.get("project")
                path_str = act.get("path", "")
                mtime: datetime = act.get("mtime", datetime.now(tz=timezone.utc))

                project_id: Optional[int] = None
                if proj_name:
                    try:
                        project_path = ""
                        for root in watch_roots:
                            candidate = Path(root) / proj_name
                            if candidate.exists():
                                project_path = str(candidate)
                                break
                        project_id = get_or_create_project(proj_name, project_path, db_path)
                    except Exception as exc:  # noqa: BLE001
                        print(
                            f"[vscode_activity] WARNING: Could not resolve project "
                            f"'{proj_name}': {exc}",
                            file=sys.stderr,
                        )

                timestamp = mtime.isoformat()
                date_str = mtime.strftime("%Y-%m-%d")

                # Deduplicate: one vscode_activity row per project per day
                existing = conn.execute(
                    """
                    SELECT id FROM activity_log
                    WHERE event_type = 'vscode_activity'
                      AND project_id IS ?
                      AND timestamp LIKE ?
                    """,
                    (project_id, f"{date_str}%"),
                ).fetchone()

                if existing:
                    continue  # already have an entry for this project today

                import json as _json
                data_blob = _json.dumps(
                    {
                        "source": "vscode_log",
                        "path": path_str,
                        "project": proj_name,
                    },
                    ensure_ascii=False,
                )

                conn.execute(
                    """
                    INSERT INTO activity_log
                        (timestamp, event_type, project_id, app_name, summary, data)
                    VALUES (?, 'vscode_activity', ?, 'vscode', ?, ?)
                    """,
                    (
                        timestamp,
                        project_id,
                        f"VSCode opened: {proj_name or path_str}",
                        data_blob,
                    ),
                )
                inserted += 1

            conn.commit()

    except sqlite3.Error as exc:
        print(
            f"[vscode_activity] ERROR: Database error: {exc}",
            file=sys.stderr,
        )

    return inserted


# ---------------------------------------------------------------------------
# Main run function
# ---------------------------------------------------------------------------

def run(
    dry_run: bool = False,
    hours: int = 24,
    config: Optional[Config] = None,
) -> None:
    """
    Main entry point: scan VSCode logs and record workspace activity.

    Parameters
    ----------
    dry_run:
        If True, print results to stdout without writing to the database.
    hours:
        How many hours back to scan log files (default: 24).
    config:
        Optional Config instance. If None, loads from config.yaml.
    """
    if config is None:
        config = Config(project_root=PROJECT_ROOT)

    since = datetime.now(tz=timezone.utc) - timedelta(hours=hours)

    # Find VSCode log directory
    log_dir = get_vscode_log_dir()
    if log_dir is None:
        print(
            "[vscode_activity] WARNING: VSCode log directory not found. "
            "Ensure VSCode is installed. Skipping.",
            file=sys.stderr,
        )
        return

    print(f"[vscode_activity] Scanning VSCode logs in: {log_dir}")
    print(f"[vscode_activity] Looking back {hours} hours (since {since.strftime('%Y-%m-%d %H:%M:%S')} UTC)")

    watch_roots = config.watch_roots
    activities = scan_log_files(log_dir, since, watch_roots)

    if not activities:
        print("[vscode_activity] No relevant workspace activity found in logs.")
        return

    if dry_run:
        print(f"\n[vscode_activity][dry-run] Found {len(activities)} workspace reference(s):")
        for act in activities:
            proj = act.get("project") or "(no project match)"
            path = act.get("path", "")
            mtime = act.get("mtime")
            mtime_str = mtime.strftime("%Y-%m-%d %H:%M") if mtime else "?"
            print(f"  [{mtime_str}] {proj}: {path}")
        print("[vscode_activity][dry-run] (no DB writes performed)")
        return

    db_path = config.get_db_path()
    n = sync_to_db(activities, db_path, config=config)
    print(f"[vscode_activity] Done. {n} new rows inserted ({len(activities)} found).")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "DayTracker: read VSCode log files to detect workspace activity. "
            "Fallback when Wakapi is not available."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/collectors/vscode_activity.py --dry-run\n"
            "  python scripts/collectors/vscode_activity.py --hours 48\n"
            "  python scripts/collectors/vscode_activity.py --dry-run --hours 24"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print results to stdout; do not write to the database.",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="How many hours back to scan (default: 24).",
    )
    args = parser.parse_args()

    try:
        run(dry_run=args.dry_run, hours=args.hours)
    except Exception as exc:  # noqa: BLE001
        print(f"[vscode_activity] FATAL: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
