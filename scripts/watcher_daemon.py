"""
scripts/watcher_daemon.py - Main DayTracker daemon that runs all collectors.

Architecture:
  Thread 1: file_watcher (watchdog Observer)
  Thread 2: window_poller (30-second loop)
  Thread 3: browser_history (60-minute interval)
  Thread 4: scheduler (runs daily_summary at config.daily_summary_time)
  Thread 5: vscode_poller (15-minute interval; only if wakapi.enabled=true)
  Main thread: status reporting every 5 minutes, graceful shutdown on Ctrl+C

On startup the daemon also ensures git post-commit hooks are installed in all
repositories found under watch_roots (idempotent).

Usage:
    python scripts/watcher_daemon.py [--dry-run]
    Runs until Ctrl+C or SIGTERM.
"""

from __future__ import annotations

import io
import signal
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
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
        sys.stdout._daytracker_wrapped = True  # type: ignore[attr-defined]
    if hasattr(sys.stderr, "buffer") and not getattr(sys.stderr, "_daytracker_wrapped", False):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr._daytracker_wrapped = True  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.config import Config  # noqa: E402

# ---------------------------------------------------------------------------
# Scheduler (schedule library with graceful fallback)
# ---------------------------------------------------------------------------
try:
    import schedule  # type: ignore
    SCHEDULE_AVAILABLE = True
except ImportError:
    SCHEDULE_AVAILABLE = False


# ---------------------------------------------------------------------------
# DayTrackerDaemon
# ---------------------------------------------------------------------------

class DayTrackerDaemon:
    """
    Manages all DayTracker background threads.

    Usage:
        daemon = DayTrackerDaemon()
        daemon.start()   # blocks until stop() is called
    """

    # Status reporting interval (seconds)
    STATUS_INTERVAL = 300  # 5 minutes

    # Browser history sync interval (seconds)
    BROWSER_SYNC_INTERVAL = 3600  # 1 hour

    def __init__(
        self,
        dry_run: bool = False,
        config: Optional[Config] = None,
    ) -> None:
        self.dry_run = dry_run
        self.config = config or Config(project_root=PROJECT_ROOT)
        self._stop_event = threading.Event()
        self._start_time: Optional[float] = None

        # Threads / observers
        self._file_observer = None
        self._window_thread: Optional[threading.Thread] = None
        self._browser_thread: Optional[threading.Thread] = None
        self._scheduler_thread: Optional[threading.Thread] = None
        self._vscode_thread: Optional[threading.Thread] = None

        # Counters (approximate; not thread-safe for exact counts)
        self._file_events_count = 0
        self._windows_count = 0
        self._browser_count = 0

    # ------------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start all daemon threads and block until stop() is called."""
        self._start_time = time.monotonic()
        mode = "DRY-RUN" if self.dry_run else "LIVE"
        print(f"[DayTracker] Starting daemon ({mode})...")
        print(f"[DayTracker] Project root: {PROJECT_ROOT}")
        print(f"[DayTracker] DB: {self.config.get_db_path()}")

        # Install git hooks (idempotent; runs once at startup)
        self._install_git_hooks()

        # Start file watcher
        self._start_file_watcher()

        # Start window poller
        self._start_window_poller()

        # Start browser history sync
        self._start_browser_sync()

        # Start VSCode/Wakapi poller (if enabled)
        self._start_vscode_thread()

        # Start scheduler
        self._start_scheduler()

        print("[DayTracker] All collectors started. Press Ctrl+C to stop.")
        print("[DayTracker] Dashboard: python scripts/datasette_setup.py --serve")

        # Main loop: status reporting
        try:
            while not self._stop_event.is_set():
                self._stop_event.wait(self.STATUS_INTERVAL)
                if not self._stop_event.is_set():
                    self._print_status()
        except Exception as exc:  # noqa: BLE001
            print(f"[DayTracker] ERROR in main loop: {exc}", file=sys.stderr)
        finally:
            self._shutdown_all()

    def stop(self) -> None:
        """Signal the daemon to stop gracefully."""
        print("[DayTracker] Stopping...")
        self._stop_event.set()

    def status(self) -> dict:
        """Return a dict with current daemon status."""
        uptime_s = (
            int(time.monotonic() - self._start_time) if self._start_time else 0
        )
        uptime_h = uptime_s // 3600
        uptime_m = (uptime_s % 3600) // 60

        # Count DB events for today
        file_events = self._count_today_events("file_change")
        windows = self._count_today_events("window_focus")
        browser = self._count_today_events("browser")
        ai_prompts = self._count_today_ai()
        git_commits = self._count_today_events("git_commit")
        vscode = (
            self._count_today_events("vscode_coding")
            + self._count_today_events("vscode_activity")
        )

        return {
            "uptime_s": uptime_s,
            "uptime_str": f"{uptime_h}h {uptime_m:02d}m",
            "file_events": file_events,
            "windows": windows,
            "browser": browser,
            "ai_prompts": ai_prompts,
            "git_commits": git_commits,
            "vscode": vscode,
            "dry_run": self.dry_run,
        }

    # ------------------------------------------------------------------
    # File watcher
    # ------------------------------------------------------------------

    def _start_file_watcher(self) -> None:
        """Start the watchdog observer."""
        try:
            from scripts.collectors.file_watcher import start_watching  # noqa: E402
            observer = start_watching(dry_run=self.dry_run, config=self.config)
            observer.start()
            self._file_observer = observer
            print("[DayTracker] File watcher started.")
        except ImportError:
            print(
                "[DayTracker] WARNING: watchdog not installed; file watcher disabled. "
                "Run: pip install watchdog",
                file=sys.stderr,
            )
        except Exception as exc:  # noqa: BLE001
            print(
                f"[DayTracker] ERROR starting file watcher: {exc}",
                file=sys.stderr,
            )

    # ------------------------------------------------------------------
    # Window poller
    # ------------------------------------------------------------------

    def _start_window_poller(self) -> None:
        """Start the window poller thread."""
        try:
            from scripts.collectors.window_poller import start_polling  # noqa: E402
            self._window_thread = start_polling(
                interval=30,
                dry_run=self.dry_run,
                config=self.config,
                stop_event=self._stop_event,
            )
            print("[DayTracker] Window poller started.")
        except Exception as exc:  # noqa: BLE001
            print(
                f"[DayTracker] ERROR starting window poller: {exc}",
                file=sys.stderr,
            )

    # ------------------------------------------------------------------
    # Browser history sync
    # ------------------------------------------------------------------

    def _start_browser_sync(self) -> None:
        """Start the browser history sync thread (runs every hour)."""
        stop = self._stop_event
        dry_run = self.dry_run
        config = self.config

        def _loop() -> None:
            print(f"[DayTracker] Browser sync started (interval={self.BROWSER_SYNC_INTERVAL}s).")
            # Run immediately on start, then every hour
            while not stop.is_set():
                try:
                    from scripts.collectors.browser_history import sync_since_last  # noqa: E402
                    sync_since_last(dry_run=dry_run, config=config, hours=1)
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"[DayTracker] ERROR in browser sync: {exc}",
                        file=sys.stderr,
                    )
                stop.wait(self.BROWSER_SYNC_INTERVAL)
            print("[DayTracker] Browser sync stopped.")

        self._browser_thread = threading.Thread(
            target=_loop,
            name="browser_sync",
            daemon=True,
        )
        self._browser_thread.start()

    # ------------------------------------------------------------------
    # Git hook installer
    # ------------------------------------------------------------------

    def _install_git_hooks(self) -> None:
        """Install DayTracker post-commit hooks in all repos under watch_roots.

        This is idempotent - repos that already have the hook are skipped.
        Runs synchronously during startup (fast: only walks directories).
        """
        if self.dry_run:
            print("[DayTracker] Skipping git hook installation in dry-run mode.")
            return
        try:
            from scripts.install_git_hook import run as install_hooks  # noqa: E402
            install_hooks(uninstall=False, dry_run=False, config=self.config)
        except Exception as exc:  # noqa: BLE001
            print(
                f"[DayTracker] WARNING: Could not install git hooks: {exc}",
                file=sys.stderr,
            )

    # ------------------------------------------------------------------
    # VSCode / Wakapi poller
    # ------------------------------------------------------------------

    # Default Wakapi polling interval (seconds).  Can be overridden via config.
    VSCODE_POLL_INTERVAL = 900  # 15 minutes

    def _start_vscode_thread(self) -> None:
        """Start the VSCode/Wakapi polling thread if Wakapi is enabled.

        Polls every ``wakapi.poll_interval_minutes`` (default 15) minutes.
        If Wakapi is disabled or not reachable, falls back to scanning
        VSCode log files once per hour using vscode_activity.py.
        """
        wakapi_cfg = self.config.get_nested("wakapi") or {}
        if not isinstance(wakapi_cfg, dict):
            wakapi_cfg = {}

        wakapi_enabled: bool = bool(wakapi_cfg.get("enabled", False))
        poll_minutes: int = int(wakapi_cfg.get("poll_interval_minutes", 15))
        poll_interval_s = poll_minutes * 60

        stop = self._stop_event
        dry_run = self.dry_run
        config = self.config

        def _loop() -> None:
            if wakapi_enabled:
                print(
                    f"[DayTracker] VSCode/Wakapi poller started "
                    f"(interval={poll_interval_s}s)."
                )
            else:
                print(
                    "[DayTracker] VSCode activity poller started "
                    "(Wakapi disabled; using log-file fallback, interval=3600s)."
                )

            # Use a longer interval for the log-file fallback
            interval_s = poll_interval_s if wakapi_enabled else 3600

            while not stop.is_set():
                try:
                    if wakapi_enabled:
                        from scripts.collectors.vscode_wakapi import run as wakapi_run  # noqa: E402
                        wakapi_run(dry_run=dry_run, config=config)
                    else:
                        from scripts.collectors.vscode_activity import run as activity_run  # noqa: E402
                        activity_run(dry_run=dry_run, hours=1, config=config)
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"[DayTracker] ERROR in VSCode poller: {exc}",
                        file=sys.stderr,
                    )
                stop.wait(interval_s)

            print("[DayTracker] VSCode poller stopped.")

        self._vscode_thread = threading.Thread(
            target=_loop,
            name="vscode_poller",
            daemon=True,
        )
        self._vscode_thread.start()

    # ------------------------------------------------------------------
    # Scheduler (daily summary)
    # ------------------------------------------------------------------

    def _start_scheduler(self) -> None:
        """Start the schedule thread for daily, weekly, and monthly summaries."""
        if not SCHEDULE_AVAILABLE:
            print(
                "[DayTracker] WARNING: schedule not installed; daily summary auto-run disabled. "
                "Run: pip install schedule",
                file=sys.stderr,
            )
            return

        summary_time = self.config.daily_summary_time
        stop = self._stop_event
        dry_run = self.dry_run
        project_root = PROJECT_ROOT

        def _run_subprocess(cmd: list, label: str) -> None:
            """Run a subprocess and log the result."""
            import subprocess
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=str(project_root),
                )
                if result.returncode == 0:
                    print(f"[DayTracker] {label} completed successfully.")
                else:
                    print(
                        f"[DayTracker] {label} failed (rc={result.returncode}):\n"
                        + result.stderr[-500:],
                        file=sys.stderr,
                    )
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[DayTracker] ERROR running {label}: {exc}",
                    file=sys.stderr,
                )

        def _run_daily_summary() -> None:
            """Run the daily summary pipeline."""
            print(f"[DayTracker] Running daily summary ({summary_time})...")
            summary_script = project_root / "scripts" / "daily_summary.py"
            if not summary_script.exists():
                print(
                    f"[DayTracker] WARNING: daily_summary.py not found at {summary_script}",
                    file=sys.stderr,
                )
                return
            cmd = [sys.executable, str(summary_script)]
            if dry_run:
                cmd.append("--dry-run")
            _run_subprocess(cmd, "daily summary")

        def _run_weekly_note() -> None:
            """Run the weekly note generator."""
            print("[DayTracker] Running weekly note (Monday schedule)...")
            weekly_script = project_root / "scripts" / "obsidian" / "weekly_note.py"
            if not weekly_script.exists():
                print(
                    f"[DayTracker] WARNING: weekly_note.py not found at {weekly_script}",
                    file=sys.stderr,
                )
                return
            cmd = [sys.executable, str(weekly_script)]
            if dry_run:
                cmd.append("--dry-run")
            _run_subprocess(cmd, "weekly note")

        def _run_monthly_note() -> None:
            """Run the monthly note generator (only on the 1st of the month)."""
            if datetime.now().day != 1:
                return
            print("[DayTracker] Running monthly note (1st of month schedule)...")
            monthly_script = project_root / "scripts" / "obsidian" / "monthly_note.py"
            if not monthly_script.exists():
                print(
                    f"[DayTracker] WARNING: monthly_note.py not found at {monthly_script}",
                    file=sys.stderr,
                )
                return
            cmd = [sys.executable, str(monthly_script)]
            if dry_run:
                cmd.append("--dry-run")
            _run_subprocess(cmd, "monthly note")

        # Daily summary at configured time every day
        schedule.every().day.at(summary_time).do(_run_daily_summary)
        print(f"[DayTracker] Daily summary scheduled at {summary_time}.")

        # Weekly note every Monday at 00:05
        schedule.every().monday.at("00:05").do(_run_weekly_note)
        print("[DayTracker] Weekly note scheduled every Monday at 00:05.")

        # Monthly note check every day at 00:10 (only executes on the 1st)
        schedule.every().day.at("00:10").do(_run_monthly_note)
        print("[DayTracker] Monthly note scheduled for the 1st of each month at 00:10.")

        def _schedule_loop() -> None:
            while not stop.is_set():
                try:
                    schedule.run_pending()
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"[DayTracker] ERROR in scheduler: {exc}",
                        file=sys.stderr,
                    )
                stop.wait(30)
            print("[DayTracker] Scheduler stopped.")

        self._scheduler_thread = threading.Thread(
            target=_schedule_loop,
            name="scheduler",
            daemon=True,
        )
        self._scheduler_thread.start()

    # ------------------------------------------------------------------
    # Status reporting
    # ------------------------------------------------------------------

    def _print_status(self) -> None:
        """Print a one-line status summary."""
        s = self.status()
        now_str = datetime.now().strftime("%H:%M:%S")
        print(
            f"[DayTracker] {now_str} | uptime: {s['uptime_str']} | "
            f"file_events: {s['file_events']} | "
            f"ai_prompts: {s['ai_prompts']} | "
            f"git_commits: {s['git_commits']} | "
            f"vscode: {s['vscode']} | "
            f"windows: {s['windows']} | "
            f"browser: {s['browser']}"
        )

    def _count_today_events(self, event_type: str) -> int:
        """Count activity_log rows for today with the given event_type."""
        if self.dry_run:
            return 0
        try:
            db_path = self.config.get_db_path()
            if not Path(db_path).exists():
                return 0
            today = datetime.now().strftime("%Y-%m-%d")
            with sqlite3.connect(db_path, timeout=5) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM activity_log WHERE event_type=? AND timestamp LIKE ?",
                    (event_type, f"{today}%"),
                ).fetchone()
            return row[0] if row else 0
        except Exception:  # noqa: BLE001
            return 0

    def _count_today_ai(self) -> int:
        """Count ai_prompts rows for today."""
        if self.dry_run:
            return 0
        try:
            db_path = self.config.get_db_path()
            if not Path(db_path).exists():
                return 0
            today = datetime.now().strftime("%Y-%m-%d")
            with sqlite3.connect(db_path, timeout=5) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM ai_prompts WHERE timestamp LIKE ?",
                    (f"{today}%",),
                ).fetchone()
            return row[0] if row else 0
        except Exception:  # noqa: BLE001
            return 0

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def _shutdown_all(self) -> None:
        """Gracefully stop all threads and observers."""
        print("[DayTracker] Shutting down all collectors...")

        # Signal all threads to stop
        self._stop_event.set()

        # Stop watchdog observer
        if self._file_observer is not None:
            try:
                self._file_observer.stop()
                self._file_observer.join(timeout=5)
                print("[DayTracker] File watcher stopped.")
            except Exception as exc:  # noqa: BLE001
                print(f"[DayTracker] WARNING: Error stopping file watcher: {exc}", file=sys.stderr)

        # Wait for threads
        for thread, name in [
            (self._window_thread, "window poller"),
            (self._browser_thread, "browser sync"),
            (self._vscode_thread, "vscode poller"),
            (self._scheduler_thread, "scheduler"),
        ]:
            if thread is not None and thread.is_alive():
                thread.join(timeout=5)
                if thread.is_alive():
                    print(f"[DayTracker] WARNING: {name} thread did not stop cleanly.")
                else:
                    print(f"[DayTracker] {name.capitalize()} stopped.")

        # Print final status
        self._print_status()
        print("[DayTracker] Daemon stopped.")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="DayTracker watcher daemon - runs all collectors in the background."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print all events to stdout; do not write to the database.",
    )
    args = parser.parse_args()

    daemon = DayTrackerDaemon(dry_run=args.dry_run)

    def _handle_signal(signum, frame) -> None:
        print(f"\n[DayTracker] Signal {signum} received.")
        daemon.stop()

    signal.signal(signal.SIGTERM, _handle_signal)
    # SIGINT is handled by KeyboardInterrupt in daemon.start()'s try/except

    try:
        daemon.start()
    except KeyboardInterrupt:
        daemon.stop()


if __name__ == "__main__":
    main()
