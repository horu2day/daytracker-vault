"""
scripts/collectors/vscode_wakapi.py - Wakapi integration for VSCode coding activity.

Polls a local Wakapi server (self-hosted WakaTime-compatible) to retrieve
VSCode coding activity. If Wakapi is not running, logs a warning and returns
empty results without crashing.

Wakapi API:
  GET /api/v1/users/current/summaries?start=YYYY-MM-DD&end=YYYY-MM-DD
  Auth: Authorization: Basic base64(:{api_key})

Config keys (config.yaml):
  wakapi:
    enabled: false
    url: "http://localhost:3000"
    api_key: ""
    poll_interval_minutes: 15

Usage:
    python scripts/collectors/vscode_wakapi.py [--dry-run] [--date YYYY-MM-DD]
    python -m scripts.collectors.vscode_wakapi [--dry-run] [--date YYYY-MM-DD]
"""

from __future__ import annotations

import base64
import io
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

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
from scripts.processors.project_mapper import get_or_create_project  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONNECT_TIMEOUT = 5  # seconds


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _make_auth_header(api_key: str) -> str:
    """
    Build the Authorization header for Wakapi.
    Wakapi accepts Basic auth with an empty username and the api_key as password.
    Format: Basic base64(:{api_key})
    """
    token = base64.b64encode(f":{api_key}".encode()).decode()
    return f"Basic {token}"


# ---------------------------------------------------------------------------
# Wakapi connectivity check
# ---------------------------------------------------------------------------

def is_wakapi_running(base_url: str) -> bool:
    """
    Check whether the local Wakapi server is reachable.

    Tries GET /api/health; any HTTP response (including 4xx/5xx) means the
    server is up. URLError (connection refused, timeout) means it is down.

    Parameters
    ----------
    base_url:
        Base URL of the Wakapi server, e.g. "http://localhost:3000".

    Returns
    -------
    bool
        True if the server responded, False on connection failure.
    """
    url = base_url.rstrip("/") + "/api/health"
    try:
        req = Request(url, method="GET")
        with urlopen(req, timeout=CONNECT_TIMEOUT):
            return True
    except HTTPError:
        # Any HTTP error (4xx/5xx) still means the server is up
        return True
    except URLError:
        return False
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Fetch summaries from Wakapi
# ---------------------------------------------------------------------------

def fetch_summaries(base_url: str, api_key: str, date: str) -> dict:
    """
    Fetch coding summaries for a specific date from the Wakapi API.

    Parameters
    ----------
    base_url:
        Base URL of the Wakapi server.
    api_key:
        Wakapi API key.
    date:
        Date string in YYYY-MM-DD format.

    Returns
    -------
    dict
        Parsed JSON response from Wakapi, or an empty dict on error.
    """
    url = (
        base_url.rstrip("/")
        + f"/api/v1/users/current/summaries?start={date}&end={date}"
    )
    headers = {
        "Authorization": _make_auth_header(api_key),
        "Content-Type": "application/json",
    }
    try:
        req = Request(url, headers=headers, method="GET")
        with urlopen(req, timeout=CONNECT_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw)
    except HTTPError as exc:
        print(
            f"[vscode_wakapi] ERROR: Wakapi API returned HTTP {exc.code}: {exc.reason}",
            file=sys.stderr,
        )
        return {}
    except URLError as exc:
        print(
            f"[vscode_wakapi] ERROR: Could not reach Wakapi at {base_url}: {exc.reason}",
            file=sys.stderr,
        )
        return {}
    except json.JSONDecodeError as exc:
        print(
            f"[vscode_wakapi] ERROR: Invalid JSON from Wakapi: {exc}",
            file=sys.stderr,
        )
        return {}
    except Exception as exc:  # noqa: BLE001
        print(
            f"[vscode_wakapi] ERROR: Unexpected error fetching summaries: {exc}",
            file=sys.stderr,
        )
        return {}


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------

def _extract_projects(summaries: dict) -> list[dict]:
    """
    Extract per-project data from a Wakapi summaries response.

    Wakapi v1 summary response structure:
    {
      "data": [
        {
          "projects": [{"name": "my-project", "total_seconds": 3600, ...}, ...],
          "languages": [...],
          "editors": [...],
          "start": "2024-01-01T00:00:00Z",
          "end":   "2024-01-01T23:59:59Z",
        }
      ]
    }
    """
    projects: list[dict] = []
    data_list = summaries.get("data", [])
    if not isinstance(data_list, list):
        return projects

    for entry in data_list:
        if not isinstance(entry, dict):
            continue
        for proj in entry.get("projects", []):
            if isinstance(proj, dict) and proj.get("name"):
                projects.append(proj)

    return projects


def _extract_languages(summaries: dict) -> list[dict]:
    """Extract language breakdown from summaries."""
    languages: list[dict] = []
    data_list = summaries.get("data", [])
    if not isinstance(data_list, list):
        return languages
    for entry in data_list:
        if isinstance(entry, dict):
            for lang in entry.get("languages", []):
                if isinstance(lang, dict) and lang.get("name"):
                    languages.append(lang)
    return languages


def _format_duration(total_seconds: float) -> str:
    """Return a human-readable duration string from a total-seconds value."""
    total_seconds = int(total_seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    if hours > 0:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


# ---------------------------------------------------------------------------
# Database upsert
# ---------------------------------------------------------------------------

def sync_to_db(
    summaries: dict,
    db_path: str,
    date: str,
    config: Optional[Config] = None,
) -> int:
    """
    Upsert Wakapi coding summary data into the activity_log table.

    Each project-level summary becomes one row with event_type='vscode_coding'.
    Existing rows for the same date+project are updated rather than duplicated.

    Parameters
    ----------
    summaries:
        Parsed Wakapi API response dict.
    db_path:
        Absolute path to worklog.db.
    date:
        Date string in YYYY-MM-DD format.
    config:
        Optional Config instance for watch_roots (used to resolve project paths).

    Returns
    -------
    int
        Number of new rows inserted (updates are not counted).
    """
    projects = _extract_projects(summaries)
    if not projects:
        print("[vscode_wakapi] No project data found in Wakapi response.")
        return 0

    languages = _extract_languages(summaries)
    lang_map: dict[str, float] = {
        lang["name"]: lang.get("total_seconds", 0)
        for lang in languages
        if lang.get("name")
    }

    watch_roots = config.watch_roots if config else []
    inserted = 0

    try:
        with sqlite3.connect(db_path, timeout=10) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")

            for proj in projects:
                proj_name: str = proj.get("name", "unknown")
                total_seconds: float = proj.get("total_seconds", 0)
                duration_str = _format_duration(total_seconds)

                # Use noon of the target date as timestamp (UTC)
                timestamp = f"{date}T12:00:00+00:00"

                # Map project name to DB project_id
                project_id: Optional[int] = None
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
                        f"[vscode_wakapi] WARNING: Could not resolve project '{proj_name}': {exc}",
                        file=sys.stderr,
                    )

                # Build data JSON blob
                data_blob = json.dumps(
                    {
                        "source": "wakapi",
                        "date": date,
                        "project": proj_name,
                        "total_seconds": total_seconds,
                        "duration": duration_str,
                        "languages": lang_map,
                        "raw": proj,
                    },
                    ensure_ascii=False,
                )

                # Check for an existing row (same date + project) to avoid duplicates
                existing = conn.execute(
                    """
                    SELECT id FROM activity_log
                    WHERE event_type = 'vscode_coding'
                      AND project_id IS ?
                      AND timestamp LIKE ?
                    """,
                    (project_id, f"{date}%"),
                ).fetchone()

                if existing:
                    conn.execute(
                        """
                        UPDATE activity_log
                           SET duration_s = ?,
                               summary    = ?,
                               data       = ?,
                               timestamp  = ?
                         WHERE id = ?
                        """,
                        (
                            int(total_seconds),
                            f"VSCode: {proj_name} ({duration_str})",
                            data_blob,
                            timestamp,
                            existing[0],
                        ),
                    )
                    print(f"[vscode_wakapi] Updated:  {proj_name} - {duration_str}")
                else:
                    conn.execute(
                        """
                        INSERT INTO activity_log
                            (timestamp, duration_s, event_type, project_id,
                             app_name, summary, data)
                        VALUES (?, ?, 'vscode_coding', ?, 'vscode', ?, ?)
                        """,
                        (
                            timestamp,
                            int(total_seconds),
                            project_id,
                            f"VSCode: {proj_name} ({duration_str})",
                            data_blob,
                        ),
                    )
                    inserted += 1
                    print(f"[vscode_wakapi] Inserted: {proj_name} - {duration_str}")

            conn.commit()

    except sqlite3.Error as exc:
        print(
            f"[vscode_wakapi] ERROR: Database error: {exc}",
            file=sys.stderr,
        )

    return inserted


# ---------------------------------------------------------------------------
# Main run function
# ---------------------------------------------------------------------------

def run(
    dry_run: bool = False,
    date: Optional[str] = None,
    config: Optional[Config] = None,
) -> None:
    """
    Main entry point: check Wakapi availability, fetch summaries, sync to DB.

    Parameters
    ----------
    dry_run:
        If True, print results to stdout without writing to the database.
    date:
        Target date in YYYY-MM-DD format. Defaults to today.
    config:
        Optional Config instance. If None, loads from config.yaml.
    """
    if config is None:
        config = Config(project_root=PROJECT_ROOT)

    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    # Read Wakapi config section
    wakapi_cfg = config.get_nested("wakapi") or {}
    if not isinstance(wakapi_cfg, dict):
        wakapi_cfg = {}

    enabled: bool = bool(wakapi_cfg.get("enabled", False))
    base_url: str = str(wakapi_cfg.get("url", "http://localhost:3000")).rstrip("/")
    api_key: str = str(wakapi_cfg.get("api_key", ""))

    if not enabled:
        print(
            "[vscode_wakapi] Wakapi is disabled in config "
            "(set wakapi.enabled: true to activate).",
            file=sys.stderr,
        )
        return

    # Check if Wakapi is reachable
    print(f"[vscode_wakapi] Checking Wakapi at {base_url}...")
    if not is_wakapi_running(base_url):
        print(
            f"[vscode_wakapi] WARNING: Wakapi server not reachable at {base_url}. "
            "Ensure Wakapi is running (see https://wakapi.dev). Skipping.",
            file=sys.stderr,
        )
        return

    print(f"[vscode_wakapi] Wakapi is running. Fetching summaries for {date}...")
    summaries = fetch_summaries(base_url, api_key, date)

    if not summaries:
        print("[vscode_wakapi] No summaries returned from Wakapi.")
        return

    projects = _extract_projects(summaries)
    languages = _extract_languages(summaries)

    if dry_run:
        print(f"\n[vscode_wakapi][dry-run] Date: {date}")
        print(f"[vscode_wakapi][dry-run] Projects ({len(projects)}):")
        for p in projects:
            secs = p.get("total_seconds", 0)
            print(f"  - {p.get('name', '?')}: {_format_duration(secs)} ({secs:.0f}s)")
        print(f"[vscode_wakapi][dry-run] Languages ({len(languages)}):")
        for lang in languages:
            secs = lang.get("total_seconds", 0)
            print(f"  - {lang.get('name', '?')}: {_format_duration(secs)}")
        print("[vscode_wakapi][dry-run] (no DB writes performed)")
        return

    db_path = config.get_db_path()
    n = sync_to_db(summaries, db_path, date, config=config)
    print(f"[vscode_wakapi] Done. {n} new rows inserted.")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="DayTracker: poll Wakapi server for VSCode coding activity.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/collectors/vscode_wakapi.py --dry-run\n"
            "  python scripts/collectors/vscode_wakapi.py --date 2026-02-22\n"
            "  python scripts/collectors/vscode_wakapi.py --dry-run --date 2026-02-21"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print results to stdout; do not write to the database.",
    )
    parser.add_argument(
        "--date",
        help="Target date in YYYY-MM-DD format (default: today).",
        default=None,
    )
    args = parser.parse_args()

    try:
        run(dry_run=args.dry_run, date=args.date)
    except Exception as exc:  # noqa: BLE001
        print(f"[vscode_wakapi] FATAL: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
