#!/usr/bin/env python3
"""
PermissionRequest hook - 권한 요청 자동 승인 (항상 허용)

Claude Code가 도구 실행 전 권한을 물어볼 때마다 자동으로 허용한다.
위험한 명령(rm -rf /, format 등)만 예외적으로 차단.

settings.local.json에 등록되어 있음 (본인 환경에서만 동작).
"""
import json
import sys

DANGEROUS_COMMANDS = [
    "rm -rf /",
    "rm -rf ~",
    "rm -rf C:",
    "format ",
    "del /f /s /q C:\\Windows",
    ":(){:|:&};:",
]

def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        # stdin 파싱 실패 → 그냥 허용 (exit 0)
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    # 위험한 Bash 명령 차단
    if tool_name == "Bash":
        command = tool_input.get("command", "")
        for dangerous in DANGEROUS_COMMANDS:
            if dangerous in command:
                print(f"[auto_allow] BLOCKED dangerous command: {command[:100]}", file=sys.stderr)
                sys.exit(2)

    # exit 0 = 허용 (Claude Code는 hook이 exit 0이면 진행)
    sys.exit(0)

if __name__ == "__main__":
    main()
