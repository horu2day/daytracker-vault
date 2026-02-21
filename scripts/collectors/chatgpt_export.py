"""
ChatGPT 공식 내보내기 파일 파서.

ChatGPT Settings > Data controls > Export data 로 받은
conversations.json 파일을 파싱해 ai_prompts 테이블에 저장.

Usage:
    python scripts/collectors/chatgpt_export.py --file conversations.json [--dry-run]
"""

import argparse
import io
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

if sys.platform == "win32" and not getattr(sys.stdout, "_daytracker_wrapped", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stdout._daytracker_wrapped = True

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))

try:
    from config import Config
    _cfg = Config()
    DB_PATH = _cfg.get_db_path()
except Exception:
    DB_PATH = str(_PROJECT_ROOT / "data" / "worklog.db")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

MAX_TEXT = 10_000


def _ts_to_iso(ts) -> str:
    """Unix timestamp → ISO 8601 로컬 시간 문자열."""
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except Exception:
        return datetime.now().isoformat()


def _extract_text(content: dict) -> str:
    """ChatGPT message content → plain text."""
    if not content:
        return ""
    parts = content.get("parts", [])
    texts = []
    for p in parts:
        if isinstance(p, str):
            texts.append(p)
        elif isinstance(p, dict) and p.get("content_type") == "text":
            texts.append(p.get("text", ""))
    return "".join(texts)[:MAX_TEXT]


def parse_export_file(filepath: str) -> list:
    """
    conversations.json 파싱.
    Returns list of dicts: session_id, timestamp, prompt_text, response_text
    """
    try:
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log.error("Cannot read file %s: %s", filepath, e)
        return []

    sessions = []
    for conv in data:
        conv_id    = conv.get("id", "")
        mapping    = conv.get("mapping", {})
        if not mapping:
            continue

        # mapping의 각 노드를 uuid → node 로 인덱싱
        nodes = {uid: node for uid, node in mapping.items() if node.get("message")}

        # user 메시지마다 바로 다음 assistant 응답을 페어링
        for uid, node in nodes.items():
            msg = node.get("message", {})
            if not msg:
                continue
            role = msg.get("author", {}).get("role", "")
            if role != "user":
                continue

            prompt_text = _extract_text(msg.get("content", {}))
            if not prompt_text.strip():
                continue

            ts = _ts_to_iso(msg.get("create_time") or 0)

            # 자식 노드 중 assistant 찾기
            response_text = ""
            for child_id in node.get("children", []):
                child = mapping.get(child_id, {})
                child_msg = child.get("message", {})
                if child_msg.get("author", {}).get("role") == "assistant":
                    response_text = _extract_text(child_msg.get("content", {}))
                    break

            sessions.append({
                "session_id":    f"{conv_id}:{uid}",
                "uuid":          uid,
                "timestamp":     ts,
                "tool":          "chatgpt",
                "prompt_text":   prompt_text,
                "response_text": response_text,
                "project":       "",
            })

    return sessions


_UPSERT_SQL = """
INSERT OR IGNORE INTO ai_prompts
    (timestamp, tool, project, prompt_text, response_text, session_id)
VALUES
    (:timestamp, :tool, :project, :prompt_text, :response_text, :session_id)
"""


def sync_to_db(sessions: list, db_path: str) -> int:
    if not sessions:
        return 0
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    try:
        conn = sqlite3.connect(db_path)
        inserted = 0
        with conn:
            for s in sessions:
                cur = conn.execute(_UPSERT_SQL, s)
                inserted += cur.rowcount
        conn.close()
        return inserted
    except sqlite3.Error as e:
        log.error("DB error: %s", e)
        return 0


def run(filepath: str, dry_run: bool = False) -> None:
    sessions = parse_export_file(filepath)
    log.info("Parsed %d user turn(s) from %s", len(sessions), filepath)

    if dry_run:
        for i, s in enumerate(sessions[:10], 1):
            print(f"[{i:03d}] {s['timestamp']} | {s['prompt_text'][:80]}")
        if len(sessions) > 10:
            print(f"  ... and {len(sessions) - 10} more")
        return

    inserted = sync_to_db(sessions, DB_PATH)
    log.info("Inserted %d new record(s)", inserted)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parse ChatGPT export file")
    parser.add_argument("--file", required=True, help="Path to conversations.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(args.file, dry_run=args.dry_run)
