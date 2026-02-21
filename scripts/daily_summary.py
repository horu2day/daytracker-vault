"""
scripts/daily_summary.py - Main entry point for daily work summary generation.

Orchestrates the full pipeline:
    1. Sync Claude Code history to DB (scripts/collectors/claude_code.py)
    2. Create AI Session notes   (scripts/obsidian/ai_session.py)
    3. Create/update Daily Note  (scripts/obsidian/daily_note.py)
    4. Update Project Notes      (scripts/obsidian/project_note.py)
    5. Update Weekly Note        (scripts/obsidian/weekly_note.py)
       - Runs automatically if today is Monday, or when --weekly flag is set.
    6. Update Monthly Note       (scripts/obsidian/monthly_note.py)
       - Runs automatically if today is 1st of month, or when --monthly flag is set.

Usage:
    python scripts/daily_summary.py [--date YYYY-MM-DD] [--dry-run]
    python scripts/daily_summary.py --weekly [--dry-run]
    python scripts/daily_summary.py --monthly [--dry-run]
"""

from __future__ import annotations

import argparse
import io
import sys
import time
from datetime import datetime
from pathlib import Path

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
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from scripts.config import Config  # type: ignore
    _cfg = Config()
except Exception:
    from config import Config  # type: ignore
    _cfg = Config()


# ---------------------------------------------------------------------------
# Step runners
# ---------------------------------------------------------------------------

def _step_sync_claude_code(date_str: str, dry_run: bool) -> dict:
    """
    Step 1: Sync Claude Code conversation history to DB.

    Runs claude_code.py as a subprocess to avoid import-time side effects
    (the module wraps sys.stdout/stderr unconditionally on Windows at import).
    """
    import subprocess

    result = {"step": "Claude Code sync", "status": "ok", "detail": ""}
    t0 = time.time()

    collector_script = _PROJECT_ROOT / "scripts" / "collectors" / "claude_code.py"
    if not collector_script.exists():
        result["status"] = "error"
        result["detail"] = f"collector script not found: {collector_script}"
        return result

    cmd = [sys.executable, str(collector_script), "--date", date_str]
    if dry_run:
        cmd.append("--dry-run")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(_PROJECT_ROOT),
        )
        elapsed = time.time() - t0
        # Print subprocess output
        if proc.stdout:
            print(proc.stdout, end="")
        if proc.stderr:
            print(proc.stderr, end="", file=sys.stderr)

        if proc.returncode != 0:
            result["status"] = "error"
            result["detail"] = f"exit code {proc.returncode} in {elapsed:.1f}s"
        else:
            result["detail"] = f"Completed in {elapsed:.1f}s"
    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["detail"] = str(exc)

    return result


def _step_ai_sessions(
    date_str: str,
    db_path: str,
    vault_path: str,
    dry_run: bool,
) -> dict:
    """Step 2: Generate AI Session notes."""
    result = {"step": "AI Session notes", "status": "ok", "detail": ""}
    t0 = time.time()

    try:
        from scripts.obsidian.ai_session import generate_ai_sessions  # type: ignore
    except ImportError:
        try:
            from obsidian.ai_session import generate_ai_sessions  # type: ignore
        except ImportError as exc:
            result["status"] = "error"
            result["detail"] = f"Could not import ai_session: {exc}"
            return result

    try:
        written = generate_ai_sessions(
            date_str=date_str,
            db_path=db_path,
            vault_path=vault_path,
            dry_run=dry_run,
        )
        elapsed = time.time() - t0
        result["detail"] = f"{len(written)} note(s) in {elapsed:.1f}s"
        result["written"] = written
    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["detail"] = str(exc)

    return result


def _step_daily_note(
    date_str: str,
    db_path: str,
    vault_path: str,
    dry_run: bool,
) -> dict:
    """Step 3: Generate or update the Daily Note."""
    result = {"step": "Daily Note", "status": "ok", "detail": ""}
    t0 = time.time()

    try:
        from scripts.obsidian.daily_note import create_or_update_daily_note  # type: ignore
    except ImportError:
        try:
            from obsidian.daily_note import create_or_update_daily_note  # type: ignore
        except ImportError as exc:
            result["status"] = "error"
            result["detail"] = f"Could not import daily_note: {exc}"
            return result

    try:
        path = create_or_update_daily_note(
            date_str=date_str,
            db_path=db_path,
            vault_path=vault_path,
            dry_run=dry_run,
        )
        elapsed = time.time() - t0
        result["detail"] = f"{path} in {elapsed:.1f}s"
        result["written"] = path
    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["detail"] = str(exc)

    return result


def _step_project_notes(
    db_path: str,
    vault_path: str,
    dry_run: bool,
) -> dict:
    """Step 4: Generate or update Project Notes."""
    result = {"step": "Project Notes", "status": "ok", "detail": ""}
    t0 = time.time()

    try:
        from scripts.obsidian.project_note import generate_project_notes  # type: ignore
    except ImportError:
        try:
            from obsidian.project_note import generate_project_notes  # type: ignore
        except ImportError as exc:
            result["status"] = "error"
            result["detail"] = f"Could not import project_note: {exc}"
            return result

    try:
        written = generate_project_notes(
            db_path=db_path,
            vault_path=vault_path,
            project_name=None,  # all projects
            dry_run=dry_run,
        )
        elapsed = time.time() - t0
        result["detail"] = f"{len(written)} note(s) in {elapsed:.1f}s"
        result["written"] = written
    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["detail"] = str(exc)

    return result


def _step_weekly_note(
    date_str: str,
    db_path: str,
    vault_path: str,
    dry_run: bool,
) -> dict:
    """Step 5: Generate or update the Weekly Note for the week containing date_str."""
    result = {"step": "Weekly Note", "status": "ok", "detail": ""}
    t0 = time.time()

    try:
        from scripts.obsidian.weekly_note import (  # type: ignore
            create_or_update_weekly_note,
            _parse_week_str,
        )
        from datetime import date as _date
        # Determine the ISO week for the given date
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        iso_year, iso_week, _ = target_date.isocalendar()
        week_label = f"{iso_year}-W{iso_week:02d}"
        monday = _date.fromisocalendar(iso_year, iso_week, 1)
        from datetime import timedelta
        sunday = monday + timedelta(days=6)
    except ImportError as exc:
        result["status"] = "error"
        result["detail"] = f"Could not import weekly_note: {exc}"
        return result
    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["detail"] = f"Week parse error: {exc}"
        return result

    try:
        path = create_or_update_weekly_note(
            week_label=week_label,
            monday=monday,
            sunday=sunday,
            db_path=db_path,
            vault_path=vault_path,
            dry_run=dry_run,
        )
        elapsed = time.time() - t0
        result["detail"] = f"{path} ({week_label}) in {elapsed:.1f}s"
        result["written"] = path
    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["detail"] = str(exc)

    return result


def _step_monthly_note(
    date_str: str,
    db_path: str,
    vault_path: str,
    dry_run: bool,
) -> dict:
    """Step 6: Generate or update the Monthly Note for the month containing date_str."""
    result = {"step": "Monthly Note", "status": "ok", "detail": ""}
    t0 = time.time()

    try:
        from scripts.obsidian.monthly_note import (  # type: ignore
            create_or_update_monthly_note,
            _parse_month_str,
        )
        import calendar
        from datetime import date as _date
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        month_label = f"{target_date.year}-{target_date.month:02d}"
        _, days_in_month = calendar.monthrange(target_date.year, target_date.month)
        first_day = _date(target_date.year, target_date.month, 1)
        last_day = _date(target_date.year, target_date.month, days_in_month)
    except ImportError as exc:
        result["status"] = "error"
        result["detail"] = f"Could not import monthly_note: {exc}"
        return result
    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["detail"] = f"Month parse error: {exc}"
        return result

    try:
        path = create_or_update_monthly_note(
            month_label=month_label,
            first_day=first_day,
            last_day=last_day,
            db_path=db_path,
            vault_path=vault_path,
            dry_run=dry_run,
        )
        elapsed = time.time() - t0
        result["detail"] = f"{path} ({month_label}) in {elapsed:.1f}s"
        result["written"] = path
    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["detail"] = str(exc)

    return result


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    date_str: str,
    dry_run: bool = False,
    run_weekly: bool = False,
    run_monthly: bool = False,
) -> None:
    """
    Execute the full daily summary pipeline.

    Parameters
    ----------
    date_str:
        Target date in YYYY-MM-DD format.
    dry_run:
        If True, print output without writing any files or DB records.
    run_weekly:
        If True, force generation of the weekly note regardless of weekday.
        If False, the weekly note is generated only if today is Monday.
    run_monthly:
        If True, force generation of the monthly note regardless of date.
        If False, the monthly note is generated only if today is the 1st.
    """
    print(f"\n{'='*60}")
    print(f"DayTracker Daily Summary Pipeline")
    print(f"Date: {date_str}")
    print(f"Mode: {'DRY-RUN' if dry_run else 'WRITE'}")
    print(f"{'='*60}\n")

    try:
        vault_path = _cfg.get_vault_path()
    except RuntimeError as exc:
        print(f"[daily_summary] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    db_path = _cfg.get_db_path()
    print(f"Vault : {vault_path}")
    print(f"DB    : {db_path}\n")

    results: list[dict] = []

    # Determine whether to run weekly/monthly steps automatically
    target_date = datetime.strptime(date_str, "%Y-%m-%d")
    is_monday = target_date.weekday() == 0  # Monday = 0
    is_first_of_month = target_date.day == 1
    should_run_weekly = run_weekly or is_monday
    should_run_monthly = run_monthly or is_first_of_month

    total_steps = 4 + (1 if should_run_weekly else 0) + (1 if should_run_monthly else 0)
    step_num = 0

    # Step 1: Sync Claude Code
    step_num += 1
    print(f"[Step {step_num}/{total_steps}] Syncing Claude Code history...")
    r1 = _step_sync_claude_code(date_str, dry_run)
    results.append(r1)
    _print_step_result(r1)

    # Step 2: AI Session notes
    step_num += 1
    print(f"\n[Step {step_num}/{total_steps}] Generating AI Session notes...")
    r2 = _step_ai_sessions(date_str, db_path, vault_path, dry_run)
    results.append(r2)
    _print_step_result(r2)

    # Step 3: Daily Note
    step_num += 1
    print(f"\n[Step {step_num}/{total_steps}] Generating Daily Note...")
    r3 = _step_daily_note(date_str, db_path, vault_path, dry_run)
    results.append(r3)
    _print_step_result(r3)

    # Step 4: Project Notes
    step_num += 1
    print(f"\n[Step {step_num}/{total_steps}] Generating Project Notes...")
    r4 = _step_project_notes(db_path, vault_path, dry_run)
    results.append(r4)
    _print_step_result(r4)

    # Step 5: Weekly Note (Monday or --weekly flag)
    if should_run_weekly:
        step_num += 1
        reason = "--weekly flag" if run_weekly else "today is Monday"
        print(f"\n[Step {step_num}/{total_steps}] Generating Weekly Note ({reason})...")
        r5 = _step_weekly_note(date_str, db_path, vault_path, dry_run)
        results.append(r5)
        _print_step_result(r5)
    else:
        print(
            "\n[weekly_note] Skipped (not Monday; use --weekly to force)."
        )

    # Step 6: Monthly Note (1st of month or --monthly flag)
    if should_run_monthly:
        step_num += 1
        reason = "--monthly flag" if run_monthly else "today is 1st of month"
        print(f"\n[Step {step_num}/{total_steps}] Generating Monthly Note ({reason})...")
        r6 = _step_monthly_note(date_str, db_path, vault_path, dry_run)
        results.append(r6)
        _print_step_result(r6)
    else:
        print(
            "\n[monthly_note] Skipped (not 1st of month; use --monthly to force)."
        )

    # Final summary
    _print_final_summary(results, date_str, vault_path, dry_run)


def _print_step_result(result: dict) -> None:
    status = result.get("status", "ok")
    step = result.get("step", "")
    detail = result.get("detail", "")
    marker = "[OK]" if status == "ok" else "[ERROR]"
    print(f"  {marker} {step}: {detail}")


def _print_final_summary(
    results: list[dict],
    date_str: str,
    vault_path: str,
    dry_run: bool,
) -> None:
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")

    errors = [r for r in results if r.get("status") == "error"]
    if errors:
        print(f"  {len(errors)} step(s) had errors:")
        for r in errors:
            print(f"    - {r['step']}: {r['detail']}")
    else:
        print("  All steps completed successfully.")

    if not dry_run:
        daily_path = Path(vault_path) / f"Daily/{date_str}.md"
        ai_dir = Path(vault_path) / "AI-Sessions"
        projects_dir = Path(vault_path) / "Projects"
        weekly_dir = Path(vault_path) / "Weekly"
        monthly_dir = Path(vault_path) / "Monthly"

        ai_count = len(list(ai_dir.glob(f"{date_str}-*.md"))) if ai_dir.exists() else 0
        proj_count = len(list(projects_dir.glob("*.md"))) if projects_dir.exists() else 0
        weekly_count = len(list(weekly_dir.glob("*.md"))) if weekly_dir.exists() else 0
        monthly_count = len(list(monthly_dir.glob("*.md"))) if monthly_dir.exists() else 0

        print(f"\n  Files in vault:")
        print(f"    Daily note   : {daily_path} ({'exists' if daily_path.exists() else 'missing'})")
        print(f"    AI Sessions  : {ai_count} note(s) for {date_str}")
        print(f"    Project notes: {proj_count} total")
        print(f"    Weekly notes : {weekly_count} total")
        print(f"    Monthly notes: {monthly_count} total")

    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the full DayTracker daily summary pipeline."
    )
    parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        default=None,
        help="Date to generate summary for (default: today in local time).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print output without writing any files or DB records.",
    )
    parser.add_argument(
        "--weekly",
        action="store_true",
        help=(
            "Force generation of the weekly note regardless of the day of week. "
            "Without this flag, the weekly note is only generated on Mondays."
        ),
    )
    parser.add_argument(
        "--monthly",
        action="store_true",
        help=(
            "Force generation of the monthly note regardless of the day of month. "
            "Without this flag, the monthly note is only generated on the 1st."
        ),
    )
    args = parser.parse_args()

    date_str = args.date or datetime.now().strftime("%Y-%m-%d")
    run_pipeline(
        date_str=date_str,
        dry_run=args.dry_run,
        run_weekly=args.weekly,
        run_monthly=args.monthly,
    )


if __name__ == "__main__":
    main()
