"""
DayTracker 데몬 시작/종료 헬퍼 스크립트.

Usage:
    python scripts/start_daemon.py           # 시작
    python scripts/start_daemon.py --stop    # 종료
    python scripts/start_daemon.py --status  # 상태 확인
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PID_FILE = ROOT / "data" / "daemon.pid"
DAEMON   = ROOT / "scripts" / "watcher_daemon.py"


def _log_path():
    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return ROOT / "data" / f"daemon_{ts}.log"


LOG_FILE = ROOT / "data" / "daemon.log"  # symlink target (latest)
ERR_FILE = ROOT / "data" / "daemon_err.log"


def _is_running(pid: int) -> bool:
    try:
        r = subprocess.run(
            ["powershell", "-c", f"Get-Process -Id {pid} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id"],
            capture_output=True, text=True
        )
        return r.stdout.strip() == str(pid)
    except Exception:
        return False


def start():
    if PID_FILE.exists():
        pid = int(PID_FILE.read_text().strip())
        if _is_running(pid):
            print(f"[DayTracker] Already running (PID={pid})")
            return

    os.makedirs(ROOT / "data", exist_ok=True)
    actual_log = _log_path()
    log = open(actual_log, "w", encoding="utf-8")

    proc = subprocess.Popen(
        [sys.executable, "-u", str(DAEMON)],
        stdout=log, stderr=log,
        cwd=str(ROOT),
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    PID_FILE.write_text(str(proc.pid))
    # latest.log 심볼릭 없이 그냥 pid 파일에 로그 경로도 저장
    (ROOT / "data" / "daemon_latest.log.path").write_text(str(actual_log))
    print(f"[DayTracker] Daemon started. PID={proc.pid}")
    print(f"  Log : {actual_log}")
    print(f"  Stop: python scripts/start_daemon.py --stop")


def stop():
    if not PID_FILE.exists():
        print("[DayTracker] No PID file found.")
        return
    pid = int(PID_FILE.read_text().strip())
    try:
        subprocess.run(
            ["powershell", "-c", f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue"],
            capture_output=True
        )
        PID_FILE.unlink(missing_ok=True)
        print(f"[DayTracker] Daemon stopped (PID={pid})")
    except Exception as e:
        print(f"[DayTracker] Error stopping daemon: {e}")


def status():
    if not PID_FILE.exists():
        print("[DayTracker] Not running (no PID file)")
        return
    pid = int(PID_FILE.read_text().strip())
    if _is_running(pid):
        print(f"[DayTracker] Running  PID={pid}")
        log_path_file = ROOT / "data" / "daemon_latest.log.path"
    actual_log = Path(log_path_file.read_text().strip()) if log_path_file.exists() else LOG_FILE
    if actual_log.exists():
            lines = actual_log.read_text(encoding="utf-8", errors="replace").splitlines()
            print("\n--- Last 10 log lines ---")
            for l in lines[-10:]:
                print(" ", l)
    else:
        print(f"[DayTracker] Not running (PID={pid} no longer exists)")
        PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stop",   action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    if args.stop:
        stop()
    elif args.status:
        status()
    else:
        start()
