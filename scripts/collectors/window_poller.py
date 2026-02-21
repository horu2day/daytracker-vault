"""
scripts/collectors/window_poller.py - Active window poller for DayTracker.

Polls the active window every N seconds using pywinctl.
Extracts app name and window title, maps to project if possible.
Inserts into activity_log with event_type='window_focus'.

VSCode title pattern: "{filename} - {foldername}" or "{foldername}"
Skip known non-work apps: explorer, task manager, clock, etc.

Usage:
    python scripts/collectors/window_poller.py [--dry-run] [--interval N]
    python -m scripts.collectors.window_poller [--dry-run] [--interval N]
"""

from __future__ import annotations

import io
import re
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
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
from scripts.processors.project_mapper import map_path_to_project, get_or_create_project  # noqa: E402

# ---------------------------------------------------------------------------
# pywinctl import (graceful degradation)
# ---------------------------------------------------------------------------
try:
    import pywinctl as pwc  # type: ignore
    PYWINCTL_AVAILABLE = True
except ImportError:
    PYWINCTL_AVAILABLE = False

# ---------------------------------------------------------------------------
# Non-work apps to skip (case-insensitive substrings in app name or title)
# ---------------------------------------------------------------------------
NON_WORK_APPS: set[str] = {
    "explorer",
    "task manager",
    "taskmgr",
    "clock",
    "calculator",
    "snipping tool",
    "magnifier",
    "narrator",
    "on-screen keyboard",
    "action center",
    "start",
    "search",
    "cortana",
    "settings",
    "control panel",
    "windows security",
    "system tray",
    "notification",
    "lockapp",
    "screensaver",
    "logonui",
}

# Patterns for known non-work window titles
NON_WORK_TITLE_PATTERNS: list[re.Pattern] = [
    re.compile(r"^\s*$"),  # empty/blank title
]

# ---------------------------------------------------------------------------
# VSCode title patterns
# ---------------------------------------------------------------------------
# "filename.ext - foldername - Visual Studio Code"
# "foldername - Visual Studio Code"
# "Visual Studio Code"
VSCODE_APP_NAMES = {"code", "code - insiders", "visual studio code"}
VSCODE_TITLE_RE = re.compile(
    r"""
    ^
    (?:(?P<filename>[^-]+?)\s+-\s+)?   # optional "filename - "
    (?P<folder>[^-]+?)                  # folder name
    \s+-\s+Visual\s+Studio\s+Code
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _parse_vscode_project(title: str) -> Optional[str]:
    """Extract the project/folder name from a VSCode window title."""
    m = VSCODE_TITLE_RE.match(title.strip())
    if m:
        folder = m.group("folder")
        if folder:
            return folder.strip()
    # Fallback: if title ends with "Visual Studio Code", take the part before last " - "
    if "Visual Studio Code" in title:
        parts = title.rsplit(" - ", maxsplit=1)
        if len(parts) > 1:
            return parts[0].strip()
    return None


# ---------------------------------------------------------------------------
# Active window detection
# ---------------------------------------------------------------------------

def get_active_window_info() -> Optional[dict]:
    """
    Return {'app': str, 'title': str, 'project': str|None} or None.

    Returns None if:
    - pywinctl is not available
    - no active window found
    - the window is a known non-work app
    """
    if not PYWINCTL_AVAILABLE:
        return None

    try:
        win = pwc.getActiveWindow()
        if win is None:
            return None

        title: str = (win.title or "").strip()
        app: str = ""

        # Try to get app name
        try:
            app = (win.getAppName() or "").strip()
        except Exception:  # noqa: BLE001
            # Fallback: use title or exe name
            try:
                app = title.split(" - ")[-1].strip() if " - " in title else title
            except Exception:  # noqa: BLE001
                app = ""

        # Skip empty
        if not title and not app:
            return None

        # Check against non-work app list
        app_lower = app.lower()
        title_lower = title.lower()

        for skip in NON_WORK_APPS:
            if skip in app_lower or skip in title_lower:
                return None

        for pattern in NON_WORK_TITLE_PATTERNS:
            if pattern.match(title):
                return None

        # Detect project
        project: Optional[str] = None

        if app_lower in VSCODE_APP_NAMES or "code" in app_lower:
            project = _parse_vscode_project(title)

        return {"app": app, "title": title, "project": project}

    except Exception as exc:  # noqa: BLE001
        print(
            f"[window_poller] WARNING: Could not get active window: {exc}",
            file=sys.stderr,
        )
        return None


# ---------------------------------------------------------------------------
# DB insert
# ---------------------------------------------------------------------------

def _insert_window_event(
    db_path: str,
    app: str,
    title: str,
    project_id: Optional[int],
    dry_run: bool,
) -> None:
    """Insert a window focus event into activity_log."""
    now_iso = datetime.now(tz=timezone.utc).isoformat()

    if dry_run:
        project_label = f"project_id={project_id}" if project_id else "no project"
        print(
            f"[window_poller][dry-run] window_focus  app={app!r}  title={title!r}  ({project_label})"
        )
        return

    try:
        with sqlite3.connect(db_path, timeout=10) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")

            conn.execute(
                """
                INSERT INTO activity_log
                    (timestamp, event_type, project_id, app_name, summary, data)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    now_iso,
                    "window_focus",
                    project_id,
                    app,
                    title[:200] if title else "",   # cap summary length
                    title,
                ),
            )
            conn.commit()
    except sqlite3.Error as exc:
        print(
            f"[window_poller] ERROR writing to DB: {exc}",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# Poll cycle
# ---------------------------------------------------------------------------

# State for tracking last seen window (to skip no-change polls)
_last_window: dict[str, str] = {}


def poll_once(
    dry_run: bool = False,
    config: Optional[Config] = None,
    db_path: Optional[str] = None,
    watch_roots: Optional[list[str]] = None,
) -> None:
    """
    Perform a single poll cycle.
    - Gets active window info
    - Skips if same window as last poll
    - Maps to project
    - Inserts into DB (or prints if dry_run)
    """
    global _last_window

    info = get_active_window_info()
    if info is None:
        return

    app = info["app"]
    title = info["title"]
    project_name = info["project"]

    # Skip if same window as last poll
    window_key = f"{app}||{title}"
    if window_key == _last_window.get("key", ""):
        return
    _last_window["key"] = window_key

    # Load config if not provided
    if config is None:
        config = Config(project_root=PROJECT_ROOT)

    if db_path is None:
        db_path = config.get_db_path()

    if watch_roots is None:
        watch_roots = config.watch_roots

    # Map project name to project_id
    project_id: Optional[int] = None

    if project_name and not dry_run:
        try:
            # Try to find a watch_root that contains a folder named project_name
            project_path = ""
            for root in watch_roots:
                candidate = Path(root) / project_name
                if candidate.exists():
                    project_path = str(candidate)
                    break

            project_id = get_or_create_project(project_name, project_path, db_path)
        except Exception as exc:  # noqa: BLE001
            print(
                f"[window_poller] WARNING: project lookup failed: {exc}",
                file=sys.stderr,
            )
    elif project_name and dry_run:
        # In dry-run, just note the project name
        info["project_label"] = project_name

    _insert_window_event(db_path, app, title, project_id, dry_run)


# ---------------------------------------------------------------------------
# Background polling thread
# ---------------------------------------------------------------------------

def start_polling(
    interval: int = 30,
    dry_run: bool = False,
    config: Optional[Config] = None,
    stop_event: Optional[threading.Event] = None,
) -> threading.Thread:
    """
    Start polling in a background thread.

    Parameters
    ----------
    interval:
        Seconds between polls (default 30).
    dry_run:
        If True, print to stdout only.
    config:
        Optional Config instance.
    stop_event:
        Optional threading.Event to signal shutdown. If None, a new one is created
        and the thread will run until the process exits.

    Returns
    -------
    threading.Thread
        The running daemon thread.
    """
    if config is None:
        config = Config(project_root=PROJECT_ROOT)

    db_path = config.get_db_path()
    watch_roots = config.watch_roots

    _stop = stop_event if stop_event is not None else threading.Event()

    def _loop() -> None:
        print(
            f"[window_poller] Started (interval={interval}s"
            f"{'  dry-run' if dry_run else ''})."
        )
        while not _stop.is_set():
            try:
                poll_once(
                    dry_run=dry_run,
                    config=config,
                    db_path=db_path,
                    watch_roots=watch_roots,
                )
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[window_poller] ERROR in poll loop: {exc}",
                    file=sys.stderr,
                )
            _stop.wait(interval)
        print("[window_poller] Stopped.")

    t = threading.Thread(target=_loop, name="window_poller", daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    import signal

    parser = argparse.ArgumentParser(
        description="DayTracker active window poller."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print window events to stdout; do not write to the database.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        metavar="N",
        help="Polling interval in seconds (default: 30).",
    )
    args = parser.parse_args()

    if not PYWINCTL_AVAILABLE:
        print(
            "[window_poller] ERROR: pywinctl is not installed. "
            "Run: pip install pywinctl",
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        f"[window_poller] Starting (interval={args.interval}s"
        f"{'  dry-run' if args.dry_run else ''})..."
    )

    stop_event = threading.Event()

    def _shutdown(signum=None, frame=None) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, _shutdown)

    t = start_polling(
        interval=args.interval,
        dry_run=args.dry_run,
        stop_event=stop_event,
    )

    try:
        while t.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        stop_event.set()

    t.join(timeout=5)
    print("[window_poller] Done.")


if __name__ == "__main__":
    main()
