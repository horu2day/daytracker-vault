"""
scripts/collectors/file_watcher.py - Filesystem change watcher for DayTracker.

Uses watchdog to monitor file changes under config.watch_roots.
Filters out config.exclude_patterns (.git/, node_modules/, __pycache__/, etc.)
On created/modified/deleted events: inserts into file_events + activity_log.
Auto-detects project from file path via project_mapper.get_or_create_project().
Debounces rapid saves: ignores duplicate events for same file within 2 seconds.

Usage:
    python scripts/collectors/file_watcher.py [--dry-run]
    python -m scripts.collectors.file_watcher [--dry-run]
"""

from __future__ import annotations

import fnmatch
import io
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
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
# Path helpers
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.config import Config  # noqa: E402
from scripts.processors.project_mapper import map_path_to_project, get_or_create_project  # noqa: E402

# ---------------------------------------------------------------------------
# Debounce state
# ---------------------------------------------------------------------------
_debounce_cache: dict[str, float] = {}
_debounce_lock = Lock()
DEBOUNCE_SECONDS = 2.0


def _is_debounced(path: str) -> bool:
    """Return True if this path was seen within the debounce window (and update cache)."""
    now = time.monotonic()
    with _debounce_lock:
        last = _debounce_cache.get(path, 0.0)
        if now - last < DEBOUNCE_SECONDS:
            return True
        _debounce_cache[path] = now
        # Prune old entries to prevent unbounded growth
        cutoff = now - 60.0
        stale = [k for k, v in _debounce_cache.items() if v < cutoff]
        for k in stale:
            del _debounce_cache[k]
    return False


# ---------------------------------------------------------------------------
# Exclusion filter
# ---------------------------------------------------------------------------

def _should_exclude(path: str, exclude_patterns: list[str]) -> bool:
    """
    Return True if the path matches any exclusion pattern.
    Checks each path component and the full path against each pattern.
    """
    p = Path(path)
    parts = p.parts

    for pattern in exclude_patterns:
        # Check each path component
        for part in parts:
            if fnmatch.fnmatch(part, pattern):
                return True
        # Check full path against pattern
        if fnmatch.fnmatch(str(p), pattern):
            return True
        # Check basename
        if fnmatch.fnmatch(p.name, pattern):
            return True

    return False


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _insert_event(
    db_path: str,
    file_path: str,
    event_type: str,
    project_id: Optional[int],
    dry_run: bool,
) -> None:
    """Insert a file event into file_events + activity_log tables."""
    now_iso = datetime.now(tz=timezone.utc).isoformat()

    if dry_run:
        project_label = f"project_id={project_id}" if project_id else "no project"
        print(
            f"[file_watcher][dry-run] {event_type.upper():<10} {file_path}  ({project_label})"
        )
        return

    try:
        with sqlite3.connect(db_path, timeout=10) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")

            # Insert into activity_log
            cursor = conn.execute(
                """
                INSERT INTO activity_log
                    (timestamp, event_type, project_id, app_name, summary, data)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    now_iso,
                    "file_change",
                    project_id,
                    "file_watcher",
                    f"{event_type}: {Path(file_path).name}",
                    file_path,
                ),
            )
            activity_id = cursor.lastrowid

            # Get file size (best-effort)
            file_size: Optional[int] = None
            try:
                if event_type != "deleted":
                    file_size = Path(file_path).stat().st_size
            except OSError:
                pass

            # Insert into file_events
            conn.execute(
                """
                INSERT INTO file_events
                    (activity_id, timestamp, file_path, event_type, project_id, file_size)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (activity_id, now_iso, file_path, event_type, project_id, file_size),
            )
            conn.commit()
    except sqlite3.Error as exc:
        print(
            f"[file_watcher] ERROR writing to DB for {file_path}: {exc}",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# Watchdog handler
# ---------------------------------------------------------------------------

try:
    from watchdog.events import FileSystemEventHandler, FileSystemEvent  # type: ignore
    from watchdog.observers import Observer  # type: ignore
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False
    # Stub so the module can still be imported for --dry-run discovery
    class FileSystemEventHandler:  # type: ignore
        pass
    class Observer:  # type: ignore
        pass
    class FileSystemEvent:  # type: ignore
        pass


class DayTrackerFileHandler(FileSystemEventHandler):
    """
    Watchdog event handler that logs filesystem events to the DayTracker DB.
    """

    def __init__(
        self,
        db_path: str,
        exclude_patterns: list[str],
        watch_roots: list[str],
        dry_run: bool = False,
    ) -> None:
        super().__init__()
        self.db_path = db_path
        self.exclude_patterns = exclude_patterns
        self.watch_roots = watch_roots
        self.dry_run = dry_run

    def _handle(self, event: "FileSystemEvent", event_type: str) -> None:
        """Common handler for all event types."""
        if event.is_directory:
            return

        path = str(event.src_path)

        # Normalise path separators
        path = path.replace("\\", "/")

        # Check exclusions
        if _should_exclude(path, self.exclude_patterns):
            return

        # Debounce rapid saves
        if _is_debounced(path):
            return

        # Map to project
        project_id: Optional[int] = None
        try:
            project_name = map_path_to_project(path, self.watch_roots)
            if project_name and not self.dry_run:
                # Find project root path
                from pathlib import Path as _Path
                norm_path = _Path(path.replace("\\", "/"))
                project_path = ""
                for root in self.watch_roots:
                    root_p = _Path(root.replace("\\", "/"))
                    try:
                        rel = norm_path.relative_to(root_p)
                        project_path = str(root_p / rel.parts[0])
                        break
                    except ValueError:
                        continue
                project_id = get_or_create_project(project_name, project_path, self.db_path)
            elif project_name and self.dry_run:
                # Just note the project name without DB write
                print(
                    f"[file_watcher][dry-run] {event_type.upper():<10} {path}  (project={project_name})"
                )
                return
        except Exception as exc:  # noqa: BLE001
            print(
                f"[file_watcher] WARNING: project mapping failed for {path}: {exc}",
                file=sys.stderr,
            )

        _insert_event(self.db_path, path, event_type, project_id, self.dry_run)

    def on_created(self, event: "FileSystemEvent") -> None:
        self._handle(event, "created")

    def on_modified(self, event: "FileSystemEvent") -> None:
        self._handle(event, "modified")

    def on_deleted(self, event: "FileSystemEvent") -> None:
        self._handle(event, "deleted")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_watching(dry_run: bool = False, config: Optional[Config] = None) -> "Observer":
    """
    Start watchdog observer on all configured watch_roots.

    Returns the Observer instance (caller is responsible for .start() / .stop() / .join()).

    Parameters
    ----------
    dry_run:
        If True, print events to stdout; do not write to DB.
    config:
        Optional Config instance.  If None, a new Config is loaded.

    Returns
    -------
    Observer
        The started watchdog Observer.  Call .stop() then .join() to shut down.
    """
    if not WATCHDOG_AVAILABLE:
        print(
            "[file_watcher] ERROR: watchdog is not installed. "
            "Run: pip install watchdog",
            file=sys.stderr,
        )
        raise ImportError("watchdog package is required")

    if config is None:
        config = Config(project_root=PROJECT_ROOT)

    db_path = config.get_db_path()
    watch_roots = config.watch_roots
    exclude_patterns = config.exclude_patterns

    if not watch_roots:
        print(
            "[file_watcher] WARNING: No watch_roots configured. "
            "Set watch_roots in config.yaml.",
            file=sys.stderr,
        )

    handler = DayTrackerFileHandler(
        db_path=db_path,
        exclude_patterns=exclude_patterns,
        watch_roots=watch_roots,
        dry_run=dry_run,
    )

    observer = Observer()
    for root in watch_roots:
        root_path = Path(root)
        if not root_path.exists():
            print(
                f"[file_watcher] WARNING: watch_root does not exist: {root}",
                file=sys.stderr,
            )
            continue
        observer.schedule(handler, str(root_path), recursive=True)
        print(f"[file_watcher] Watching: {root_path}")

    return observer


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    import signal

    parser = argparse.ArgumentParser(
        description="DayTracker filesystem watcher daemon."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print events to stdout; do not write to the database.",
    )
    args = parser.parse_args()

    if not WATCHDOG_AVAILABLE:
        print(
            "[file_watcher] ERROR: watchdog is not installed. "
            "Run: pip install watchdog",
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        f"[file_watcher] Starting{'  (dry-run)' if args.dry_run else ''}..."
    )

    observer = start_watching(dry_run=args.dry_run)
    observer.start()

    stop_event = [False]

    def _shutdown(signum=None, frame=None) -> None:
        stop_event[0] = True

    signal.signal(signal.SIGTERM, _shutdown)

    try:
        while not stop_event[0]:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    print("[file_watcher] Stopping...")
    observer.stop()
    observer.join()
    print("[file_watcher] Stopped.")


if __name__ == "__main__":
    main()
