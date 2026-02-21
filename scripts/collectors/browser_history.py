"""
scripts/collectors/browser_history.py - Browser history collector for DayTracker.

Reads Chrome/Edge history periodically by copying the locked SQLite DB to a temp file.
Queries urls table for entries newer than the last sync time.
Stores last sync time in data/browser_sync_state.json.
Inserts into activity_log with event_type='browser'.

Chrome timestamp: microseconds since 1601-01-01 (Windows FILETIME).
Conversion: (ts - 11644473600 * 1000000) / 1000000 -> Unix timestamp

Usage:
    python scripts/collectors/browser_history.py [--dry-run] [--hours N]
    python -m scripts.collectors.browser_history [--dry-run] [--hours N]
"""

from __future__ import annotations

import io
import json
import shutil
import sqlite3
import sys
import tempfile
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

# ---------------------------------------------------------------------------
# Chrome/Edge history database paths
# ---------------------------------------------------------------------------
CHROME_HISTORY_PATHS = [
    # Windows Chrome
    Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "User Data" / "Default" / "History",
    # Windows Edge
    Path.home() / "AppData" / "Local" / "Microsoft" / "Edge" / "User Data" / "Default" / "History",
    # Mac Chrome
    Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "Default" / "History",
    # Mac Edge
    Path.home() / "Library" / "Application Support" / "Microsoft Edge" / "Default" / "History",
    # Linux Chrome
    Path.home() / ".config" / "google-chrome" / "Default" / "History",
    # Linux Chromium
    Path.home() / ".config" / "chromium" / "Default" / "History",
]

# Chrome epoch offset: seconds from 1601-01-01 to 1970-01-01
CHROME_EPOCH_OFFSET_US = 11644473600 * 1_000_000


def _chrome_ts_to_unix(chrome_ts: int) -> float:
    """Convert Chrome microsecond timestamp to Unix timestamp (seconds)."""
    return (chrome_ts - CHROME_EPOCH_OFFSET_US) / 1_000_000


def _unix_to_chrome_ts(unix_ts: float) -> int:
    """Convert Unix timestamp to Chrome microsecond timestamp."""
    return int(unix_ts * 1_000_000 + CHROME_EPOCH_OFFSET_US)


def _find_chrome_history() -> Optional[Path]:
    """Return the first Chrome/Edge History file that exists."""
    for p in CHROME_HISTORY_PATHS:
        if p.exists():
            return p
    return None


# ---------------------------------------------------------------------------
# Sync state (tracks last sync time per browser profile)
# ---------------------------------------------------------------------------

def _get_sync_state_path(config: Optional[Config] = None) -> Path:
    """Return the path to the browser sync state JSON file."""
    if config is not None:
        root = Path(config.get_project_root())
    else:
        root = PROJECT_ROOT
    return root / "data" / "browser_sync_state.json"


def _load_sync_state(state_path: Path) -> dict:
    """Load the sync state from disk."""
    if state_path.exists():
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_sync_state(state_path: Path, state: dict) -> None:
    """Persist the sync state to disk."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except OSError as exc:
        print(
            f"[browser_history] WARNING: Could not save sync state: {exc}",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# Core history reader
# ---------------------------------------------------------------------------

def get_chrome_history(
    hours: int = 1,
    history_path: Optional[Path] = None,
) -> list[dict]:
    """
    Return a list of Chrome/Edge history entries from the last `hours` hours.

    Each entry is a dict with keys:
        url (str), title (str), visit_time (datetime, UTC), visit_count (int)

    Parameters
    ----------
    hours:
        How many hours back to fetch (default 1).
    history_path:
        Override the Chrome History file path.

    Returns
    -------
    list[dict]
        List of history entries, newest first.
    """
    if history_path is None:
        history_path = _find_chrome_history()

    if history_path is None:
        print(
            "[browser_history] WARNING: No Chrome/Edge History file found.",
            file=sys.stderr,
        )
        return []

    # Calculate cutoff timestamp
    cutoff_unix = time.time() - hours * 3600
    cutoff_chrome = _unix_to_chrome_ts(cutoff_unix)

    # Copy to a temp file because Chrome locks the original
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".History.db", delete=False) as tmp:
            tmp_path = tmp.name

        shutil.copy2(str(history_path), tmp_path)

        entries: list[dict] = []

        with sqlite3.connect(tmp_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row

            # Query urls and visits joined
            rows = conn.execute(
                """
                SELECT
                    u.url,
                    u.title,
                    u.visit_count,
                    v.visit_time
                FROM urls u
                JOIN visits v ON u.id = v.url
                WHERE v.visit_time >= ?
                ORDER BY v.visit_time DESC
                """,
                (cutoff_chrome,),
            ).fetchall()

            for row in rows:
                chrome_ts = row["visit_time"]
                if chrome_ts <= 0:
                    continue
                unix_ts = _chrome_ts_to_unix(chrome_ts)
                visit_time = datetime.fromtimestamp(unix_ts, tz=timezone.utc)

                entries.append({
                    "url": row["url"] or "",
                    "title": row["title"] or "",
                    "visit_time": visit_time,
                    "visit_count": row["visit_count"] or 1,
                    "chrome_ts": chrome_ts,
                })

        return entries

    except sqlite3.Error as exc:
        print(
            f"[browser_history] ERROR reading Chrome history: {exc}",
            file=sys.stderr,
        )
        return []
    except OSError as exc:
        print(
            f"[browser_history] ERROR copying Chrome history: {exc}",
            file=sys.stderr,
        )
        return []
    finally:
        if tmp_path:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# DB insert
# ---------------------------------------------------------------------------

def sync_to_db(
    entries: list[dict],
    db_path: str,
    dry_run: bool = False,
) -> int:
    """
    Upsert browser history entries into activity_log.

    Deduplicates by (url, visit_time) to avoid re-inserting on repeated runs.

    Parameters
    ----------
    entries:
        List of dicts from get_chrome_history().
    db_path:
        Path to worklog.db.
    dry_run:
        If True, only print; do not write to DB.

    Returns
    -------
    int
        Number of entries newly inserted.
    """
    if not entries:
        return 0

    if dry_run:
        for entry in entries:
            ts_str = entry["visit_time"].strftime("%Y-%m-%d %H:%M:%S UTC")
            title = entry["title"][:80] if entry["title"] else "(no title)"
            url = entry["url"][:80] if entry["url"] else ""
            print(f"[browser_history][dry-run] {ts_str}  {title!r}  {url}")
        return len(entries)

    inserted = 0

    try:
        with sqlite3.connect(db_path, timeout=10) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")

            for entry in entries:
                ts_iso = entry["visit_time"].isoformat()
                url = entry["url"]
                title = entry["title"]

                # Check for duplicate
                existing = conn.execute(
                    """
                    SELECT id FROM activity_log
                    WHERE event_type = 'browser'
                      AND timestamp = ?
                      AND data = ?
                    LIMIT 1
                    """,
                    (ts_iso, url),
                ).fetchone()

                if existing:
                    continue

                conn.execute(
                    """
                    INSERT INTO activity_log
                        (timestamp, event_type, project_id, app_name, summary, data)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ts_iso,
                        "browser",
                        None,
                        "chrome",
                        title[:200] if title else url[:200],
                        url,
                    ),
                )
                inserted += 1

            conn.commit()

    except sqlite3.Error as exc:
        print(
            f"[browser_history] ERROR writing to DB: {exc}",
            file=sys.stderr,
        )

    return inserted


# ---------------------------------------------------------------------------
# Incremental sync (reads since last sync time)
# ---------------------------------------------------------------------------

def sync_since_last(
    dry_run: bool = False,
    config: Optional[Config] = None,
    hours: int = 1,
) -> int:
    """
    Fetch browser history since last sync and insert new entries into DB.

    Updates the sync state after a successful sync.

    Returns the number of new entries inserted.
    """
    if config is None:
        config = Config(project_root=PROJECT_ROOT)

    db_path = config.get_db_path()
    state_path = _get_sync_state_path(config)
    state = _load_sync_state(state_path)

    # Determine cutoff: max of (last sync time, now - hours)
    last_sync_unix = state.get("last_sync_unix", 0.0)
    hours_cutoff_unix = time.time() - hours * 3600
    cutoff_unix = max(last_sync_unix, hours_cutoff_unix)
    effective_hours = max(1, int((time.time() - cutoff_unix) / 3600) + 1)

    entries = get_chrome_history(hours=effective_hours)

    # Filter to entries newer than last_sync_unix
    if last_sync_unix > 0:
        entries = [
            e for e in entries
            if e["visit_time"].timestamp() > last_sync_unix
        ]

    if not entries:
        print("[browser_history] No new browser history entries.")
        return 0

    count = sync_to_db(entries, db_path, dry_run=dry_run)

    if not dry_run and count > 0:
        # Update sync state
        newest_ts = max(e["visit_time"].timestamp() for e in entries)
        state["last_sync_unix"] = newest_ts
        _save_sync_state(state_path, state)

    print(f"[browser_history] Synced {count} new browser history entries.")
    return count


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="DayTracker browser history collector."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print entries to stdout; do not write to the database.",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=1,
        metavar="N",
        help="Fetch history from the last N hours (default: 1).",
    )
    args = parser.parse_args()

    print(
        f"[browser_history] Fetching last {args.hours}h of browser history"
        f"{'  (dry-run)' if args.dry_run else ''}..."
    )

    config = Config(project_root=PROJECT_ROOT)

    entries = get_chrome_history(hours=args.hours)

    if not entries:
        print("[browser_history] No browser history found.")
        return

    print(f"[browser_history] Found {len(entries)} entries.")

    db_path = config.get_db_path()
    count = sync_to_db(entries, db_path, dry_run=args.dry_run)

    if args.dry_run:
        print(f"[browser_history] Would insert {count} entries.")
    else:
        print(f"[browser_history] Inserted {count} new entries.")


if __name__ == "__main__":
    main()
