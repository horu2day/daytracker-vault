"""
scripts/datasette_setup.py - Set up Datasette for browsing worklog.db.

Provides a lightweight CLI to install Datasette, write the dashboard metadata
file, and launch the browser-accessible worklog dashboard.

Usage:
    python scripts/datasette_setup.py --install          # pip install datasette
    python scripts/datasette_setup.py --serve            # launch on port 8001
    python scripts/datasette_setup.py --serve --port 9000
    python scripts/datasette_setup.py --write-metadata   # only write JSON file
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Windows UTF-8 stdout
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    if hasattr(sys.stdout, "buffer") and not getattr(sys.stdout, "_daytracker_wrapped", False):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stdout._daytracker_wrapped = True  # type: ignore[attr-defined]
    if hasattr(sys.stderr, "buffer") and not getattr(sys.stderr, "_daytracker_wrapped", False):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
        sys.stderr._daytracker_wrapped = True  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

METADATA_PATH = PROJECT_ROOT / "datasette_metadata.json"

# ---------------------------------------------------------------------------
# Metadata content (the dashboard configuration)
# ---------------------------------------------------------------------------

METADATA: dict = {
    "title": "DayTracker Dashboard",
    "description": "개인 작업 기록 데이터베이스",
    "databases": {
        "worklog": {
            "tables": {
                "ai_prompts": {
                    "title": "AI 프롬프트 기록",
                    "description": "Claude Code, ChatGPT, Gemini 대화 기록",
                },
                "activity_log": {
                    "title": "활동 로그",
                    "description": "파일 변경, 창 활동, 브라우저 방문 기록",
                },
                "file_events": {
                    "title": "파일 변경 이벤트",
                },
                "projects": {
                    "title": "프로젝트 목록",
                },
            }
        }
    },
    "queries": {
        "오늘_요약": {
            "sql": (
                "SELECT date(timestamp,'localtime') as date, event_type, COUNT(*) as count "
                "FROM activity_log "
                "WHERE date(timestamp,'localtime') = date('now','localtime') "
                "GROUP BY date, event_type "
                "ORDER BY count DESC"
            ),
            "title": "오늘 활동 요약",
        },
        "프로젝트별_AI세션": {
            "sql": (
                "SELECT project, COUNT(*) as sessions, MAX(timestamp) as last_activity "
                "FROM ai_prompts "
                "GROUP BY project "
                "ORDER BY sessions DESC"
            ),
            "title": "프로젝트별 AI 세션",
        },
        "최근_파일변경": {
            "sql": (
                "SELECT datetime(timestamp,'localtime') as time, file_path, event_type "
                "FROM file_events "
                "ORDER BY timestamp DESC "
                "LIMIT 50"
            ),
            "title": "최근 파일 변경 50건",
        },
        "일별_활동량": {
            "sql": (
                "SELECT date(timestamp,'localtime') as date, COUNT(*) as events "
                "FROM activity_log "
                "GROUP BY date "
                "ORDER BY date DESC "
                "LIMIT 30"
            ),
            "title": "최근 30일 일별 활동량",
        },
    },
}


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------

def is_installed() -> bool:
    """Return True if Datasette is importable / installed."""
    try:
        import importlib.util
        return importlib.util.find_spec("datasette") is not None
    except Exception:
        return False


def install() -> bool:
    """
    Run ``pip install datasette`` if not already installed.

    Returns:
        True on success, False on failure.
    """
    if is_installed():
        print("[datasette_setup] datasette is already installed.")
        return True

    print("[datasette_setup] Installing datasette via pip...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "datasette"],
        capture_output=False,  # show pip output in real time
    )
    if result.returncode == 0:
        print("[datasette_setup] datasette installed successfully.")
        return True
    else:
        print(
            "[datasette_setup] ERROR: pip install failed (exit code "
            f"{result.returncode}).",
            file=sys.stderr,
        )
        return False


def write_metadata(path: str | Path = METADATA_PATH) -> None:
    """
    Write the Datasette metadata JSON file to *path*.

    Overwrites any existing file.

    Args:
        path: Destination path (default: ``<project_root>/datasette_metadata.json``).
    """
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(METADATA, fh, ensure_ascii=True, indent=2)
    print(f"[datasette_setup] Metadata written to {dest}")


def _get_db_path() -> str:
    """Return the worklog.db path from config, or a sensible default."""
    try:
        from scripts.config import Config  # noqa: E402
        return Config(project_root=PROJECT_ROOT).get_db_path()
    except Exception:
        return str(PROJECT_ROOT / "data" / "worklog.db")


def serve(port: int = 8001) -> None:
    """
    Launch Datasette in blocking mode (Ctrl+C to stop).

    Writes the metadata file first, then runs:
        datasette data/worklog.db --metadata datasette_metadata.json --port <port>

    Args:
        port: TCP port Datasette should listen on (default 8001).
    """
    if not is_installed():
        print(
            "[datasette_setup] datasette is not installed. "
            "Run: python scripts/datasette_setup.py --install",
            file=sys.stderr,
        )
        sys.exit(1)

    write_metadata()

    db_path = _get_db_path()
    if not Path(db_path).exists():
        print(
            f"[datasette_setup] WARNING: worklog.db not found at {db_path}. "
            "Datasette will start but show empty tables.",
            file=sys.stderr,
        )

    cmd = [
        sys.executable, "-m", "datasette",
        "serve",
        db_path,
        "--metadata", str(METADATA_PATH),
        "--port", str(port),
        "--host", "127.0.0.1",
    ]

    print(f"[datasette_setup] Launching Datasette on http://127.0.0.1:{port}")
    print(f"[datasette_setup] DB: {db_path}")
    print("[datasette_setup] Press Ctrl+C to stop.\n")

    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print("\n[datasette_setup] Datasette stopped.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DayTracker Datasette dashboard setup utility"
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Install datasette via pip if not already installed.",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Start the Datasette dashboard (blocking).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8001,
        metavar="PORT",
        help="Port for the Datasette server (default: 8001).",
    )
    parser.add_argument(
        "--write-metadata",
        action="store_true",
        help="Write/overwrite datasette_metadata.json without starting the server.",
    )
    return parser


def run() -> None:
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.install:
        success = install()
        sys.exit(0 if success else 1)

    if args.write_metadata:
        write_metadata()
        sys.exit(0)

    if args.serve:
        serve(port=args.port)
        sys.exit(0)

    # No flag given: print help
    parser.print_help()


if __name__ == "__main__":
    run()
