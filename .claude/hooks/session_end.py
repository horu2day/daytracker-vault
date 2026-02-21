#!/usr/bin/env python3
"""
Stop hook - Claude Code 세션 종료 시 자동 실행
이 세션에서 변경된 파일들을 집계하고, AI Session Note 생성을 트리거한다.
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
    transcript_path = data.get("transcript_path", "")

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
            (timestamp, "session_end", "claude-code", f"세션 종료: {project_name}",
             json.dumps({"session_id": session_id, "cwd": cwd, "transcript_path": transcript_path}))
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

    # AI Session Note 생성 스크립트가 있으면 비동기 실행
    ai_session_script = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "obsidian", "ai_session.py")
    ai_session_script = os.path.normpath(ai_session_script)
    if os.path.exists(ai_session_script):
        import subprocess
        subprocess.Popen(
            [sys.executable, ai_session_script, "--session-id", session_id],
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

    sys.exit(0)

if __name__ == "__main__":
    main()
