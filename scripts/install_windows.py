"""
scripts/install_windows.py - Register watcher_daemon.py as a Windows Task Scheduler task.

Registers DayTracker-Watcher to run at user logon via Windows Task Scheduler.

Usage:
    python scripts/install_windows.py            # install the task
    python scripts/install_windows.py --uninstall # remove the task
    python scripts/install_windows.py --status    # check task status
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

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
# Constants
# ---------------------------------------------------------------------------
TASK_NAME = "DayTracker-Watcher"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DAEMON_SCRIPT = PROJECT_ROOT / "scripts" / "watcher_daemon.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_python_exe() -> str:
    """Return the absolute path to the current Python interpreter."""
    return sys.executable


def _run_schtasks(*args: str) -> tuple[int, str, str]:
    """
    Run schtasks with the given arguments.

    Returns (returncode, stdout, stderr).
    """
    cmd = ["schtasks"] + list(args)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        return 1, "", "schtasks command not found. Are you on Windows?"
    except Exception as exc:  # noqa: BLE001
        return 1, "", str(exc)


def _task_exists(task_name: str) -> bool:
    """Return True if the task already exists in Task Scheduler."""
    rc, out, _ = _run_schtasks("/Query", "/TN", task_name, "/FO", "LIST")
    return rc == 0


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------

def install_task(dry_run: bool = False) -> bool:
    """
    Register the DayTracker-Watcher task with Windows Task Scheduler.

    - Task name: DayTracker-Watcher
    - Trigger: At logon (current user)
    - Action: python {project_root}/scripts/watcher_daemon.py
    - Working directory: project root

    Returns True on success.
    """
    if sys.platform != "win32":
        print(
            "[install_windows] ERROR: This script is for Windows only.",
            file=sys.stderr,
        )
        return False

    python_exe = _get_python_exe()
    daemon_script = str(DAEMON_SCRIPT)
    working_dir = str(PROJECT_ROOT)

    print(f"[install_windows] Task name     : {TASK_NAME}")
    print(f"[install_windows] Python        : {python_exe}")
    print(f"[install_windows] Script        : {daemon_script}")
    print(f"[install_windows] Working dir   : {working_dir}")
    print(f"[install_windows] Trigger       : At logon")

    if dry_run:
        print("[install_windows] [dry-run] Would register task (skipping).")
        return True

    # Check if already exists
    if _task_exists(TASK_NAME):
        print(f"[install_windows] Task '{TASK_NAME}' already exists. Updating...")
        # Delete existing first
        rc, out, err = _run_schtasks("/Delete", "/TN", TASK_NAME, "/F")
        if rc != 0:
            print(
                f"[install_windows] ERROR: Could not delete existing task: {err}",
                file=sys.stderr,
            )
            return False

    # Build the schtasks command
    # /SC ONLOGON       - trigger at user logon
    # /RL HIGHEST       - run with highest privileges available
    # /F                - force create, suppress prompts
    # /TR               - task run action
    # /ST 00:00         - required for some Windows versions with ONLOGON
    action = f'"{python_exe}" "{daemon_script}"'

    rc, out, err = _run_schtasks(
        "/Create",
        "/TN", TASK_NAME,
        "/SC", "ONLOGON",
        "/TR", action,
        "/RL", "HIGHEST",
        "/F",
        "/SD", "01/01/2024",  # start date (ignored for ONLOGON but required syntax)
    )

    if rc == 0:
        print(f"[install_windows] Task '{TASK_NAME}' registered successfully.")
        print(
            "[install_windows] The daemon will start automatically at next Windows logon."
        )
        print(
            "[install_windows] To start it now, run:\n"
            f"    schtasks /Run /TN {TASK_NAME}"
        )

        # Set working directory via XML manipulation (schtasks /Create doesn't support it directly)
        _set_working_directory(TASK_NAME, working_dir)

        return True
    else:
        print(
            f"[install_windows] ERROR: schtasks /Create failed (rc={rc}):\n{err}",
            file=sys.stderr,
        )
        return False


def _set_working_directory(task_name: str, working_dir: str) -> None:
    """
    Update the working directory for an existing scheduled task using XML export/import.
    """
    import tempfile
    import os

    try:
        # Export task XML
        with tempfile.NamedTemporaryFile(
            suffix=".xml", delete=False, mode="w", encoding="utf-16"
        ) as tmp:
            tmp_path = tmp.name

        rc, out, err = _run_schtasks("/Query", "/TN", task_name, "/XML")
        if rc != 0 or not out.strip():
            print(
                "[install_windows] WARNING: Could not export task XML to set working directory.",
                file=sys.stderr,
            )
            return

        # Inject WorkingDirectory into the XML
        xml = out
        working_dir_tag = f"<WorkingDirectory>{working_dir}</WorkingDirectory>"

        if "<WorkingDirectory>" in xml:
            # Replace existing
            import re
            xml = re.sub(
                r"<WorkingDirectory>.*?</WorkingDirectory>",
                working_dir_tag,
                xml,
            )
        else:
            # Insert before </Exec>
            xml = xml.replace("</Exec>", f"  {working_dir_tag}\n          </Exec>")

        # Write modified XML
        with open(tmp_path, "w", encoding="utf-16") as f:
            f.write(xml)

        # Delete and re-import
        _run_schtasks("/Delete", "/TN", task_name, "/F")
        rc2, out2, err2 = _run_schtasks("/Create", "/TN", task_name, "/XML", tmp_path, "/F")

        if rc2 == 0:
            print(f"[install_windows] Working directory set to: {working_dir}")
        else:
            print(
                f"[install_windows] WARNING: Could not set working directory: {err2}",
                file=sys.stderr,
            )

    except Exception as exc:  # noqa: BLE001
        print(
            f"[install_windows] WARNING: Error setting working directory: {exc}",
            file=sys.stderr,
        )
    finally:
        try:
            if "tmp_path" in locals():
                Path(tmp_path).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------

def uninstall_task() -> bool:
    """
    Remove the DayTracker-Watcher task from Task Scheduler.

    Returns True on success.
    """
    if sys.platform != "win32":
        print(
            "[install_windows] ERROR: This script is for Windows only.",
            file=sys.stderr,
        )
        return False

    if not _task_exists(TASK_NAME):
        print(f"[install_windows] Task '{TASK_NAME}' does not exist (nothing to remove).")
        return True

    rc, out, err = _run_schtasks("/Delete", "/TN", TASK_NAME, "/F")

    if rc == 0:
        print(f"[install_windows] Task '{TASK_NAME}' removed successfully.")
        return True
    else:
        print(
            f"[install_windows] ERROR: Could not remove task (rc={rc}):\n{err}",
            file=sys.stderr,
        )
        return False


# ---------------------------------------------------------------------------
# Status check
# ---------------------------------------------------------------------------

def check_status() -> None:
    """Print the current status of the scheduled task."""
    if sys.platform != "win32":
        print("[install_windows] ERROR: This script is for Windows only.", file=sys.stderr)
        return

    rc, out, err = _run_schtasks("/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V")

    if rc == 0:
        print(f"[install_windows] Task '{TASK_NAME}' exists:")
        # Print relevant lines
        for line in out.splitlines():
            line = line.strip()
            if line and any(
                kw in line
                for kw in [
                    "Task Name", "Status", "Next Run", "Last Run", "Run As User",
                    "Task To Run", "Scheduled Task State",
                ]
            ):
                print(f"  {line}")
    else:
        print(
            f"[install_windows] Task '{TASK_NAME}' does not exist or could not be queried."
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Install/uninstall DayTracker-Watcher as a Windows Task Scheduler task."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove the scheduled task.",
    )
    group.add_argument(
        "--status",
        action="store_true",
        help="Check the status of the scheduled task.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without actually creating/deleting tasks.",
    )
    args = parser.parse_args()

    if args.status:
        check_status()
    elif args.uninstall:
        success = uninstall_task()
        sys.exit(0 if success else 1)
    else:
        success = install_task(dry_run=args.dry_run)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
