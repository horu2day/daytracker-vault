#!/usr/bin/env python3
"""
PostToolUse hook - Bash 실행 후 자동 실행
실행된 터미널 명령어를 worklog.db의 activity_log 테이블에 기록한다.
"""
import json
import sys
import os
from datetime import datetime

# 기록하지 않을 명령어 패턴 (노이즈 제거)
SKIP_PATTERNS = [
    "echo ",
    "cat ",
    "ls ",
    "pwd",
    "which ",
    "type ",
]

def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    command = tool_input.get("command", "").strip()
    cwd = data.get("cwd", "")

    if not command:
        sys.exit(0)

    # 노이즈 명령어 제외
    for pattern in SKIP_PATTERNS:
        if command.startswith(pattern):
            sys.exit(0)

    db_path = os.path.join(os.path.dirname(__file__), "..", "..", "data", "worklog.db")
    db_path = os.path.normpath(db_path)
    if not os.path.exists(db_path):
        sys.exit(0)

    try:
        import sqlite3
        timestamp = datetime.now().isoformat()
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO activity_log (timestamp, event_type, app_name, summary, data) VALUES (?, ?, ?, ?, ?)",
            (timestamp, "bash", "terminal", command[:500], json.dumps({"cwd": cwd}))
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

    sys.exit(0)

if __name__ == "__main__":
    main()
