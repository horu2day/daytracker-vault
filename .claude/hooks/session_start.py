#!/usr/bin/env python3
"""
SessionStart hook - Claude Code 세션 시작 시 자동 실행
세션 시작을 activity_log에 기록하고, PROGRESS.md와 PLAN.md 요약을 출력한다.
"""
import json
import sys
import os
from datetime import datetime

def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    session_id = data.get("session_id", "")
    cwd = data.get("cwd", "")
    source = data.get("source", "startup")  # startup, resume, clear, compact

    # resume이나 compact는 기록 스킵 (중복 방지)
    if source in ("resume", "compact"):
        sys.exit(0)

    db_path = os.path.join(os.path.dirname(__file__), "..", "..", "data", "worklog.db")
    db_path = os.path.normpath(db_path)
    if not os.path.exists(db_path):
        sys.exit(0)

    try:
        import sqlite3
        timestamp = datetime.now().isoformat()
        project_name = os.path.basename(cwd) if cwd else "unknown"

        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO activity_log (timestamp, event_type, app_name, summary, data) VALUES (?, ?, ?, ?, ?)",
            (timestamp, "session_start", "claude-code", f"세션 시작: {project_name}",
             json.dumps({"session_id": session_id, "cwd": cwd, "source": source}))
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

    sys.exit(0)

if __name__ == "__main__":
    main()
