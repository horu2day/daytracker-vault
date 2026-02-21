#!/usr/bin/env python3
"""
PostToolUse hook - Write/Edit 후 자동 실행
수정된 파일 경로를 worklog.db의 file_events 테이블에 기록한다.
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

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    cwd = data.get("cwd", "")

    file_path = tool_input.get("file_path", "")
    if not file_path:
        sys.exit(0)

    # worklog.db가 없으면 조용히 종료 (Phase 1 미완성 상태)
    db_path = os.path.join(os.path.dirname(__file__), "..", "..", "data", "worklog.db")
    db_path = os.path.normpath(db_path)
    if not os.path.exists(db_path):
        sys.exit(0)

    try:
        import sqlite3
        event_type = "modified" if tool_name == "Edit" else "created"
        timestamp = datetime.now().isoformat()
        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0

        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO file_events (timestamp, file_path, event_type, file_size) VALUES (?, ?, ?, ?)",
            (timestamp, file_path, event_type, file_size)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # 수집 실패 시 조용히 종료

    sys.exit(0)

if __name__ == "__main__":
    main()
