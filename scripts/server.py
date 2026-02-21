"""
DayTracker Local Receiver Server

Accepts AI prompt data from the browser extension and saves to worklog.db.

Usage:
    python scripts/server.py [--port 7331] [--dry-run]
"""

import io
import json
import logging
import os
import re
import sqlite3
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# Windows UTF-8 stdout
if sys.platform == "win32" and not getattr(sys.stdout, "_daytracker_wrapped", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stdout._daytracker_wrapped = True
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    sys.stderr._daytracker_wrapped = True

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))

try:
    from config import Config
    _cfg = Config()
    DB_PATH = _cfg.get_db_path()
    _extra_patterns = _cfg.sensitive_patterns
except Exception:
    DB_PATH = str(_PROJECT_ROOT / "data" / "worklog.db")
    _extra_patterns = []

try:
    from processors.sensitive_filter import SensitiveFilter as _SF
    _sensitive_filter = _SF(extra_patterns=_extra_patterns)
except Exception:
    _sensitive_filter = None  # type: ignore[assignment]

VERSION = "1.0.0"
_DRY_RUN = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sensitive pattern masking
# ---------------------------------------------------------------------------

def mask_sensitive(text: str) -> str:
    """Apply SensitiveFilter (or simple regex fallback) to mask secrets."""
    if not text:
        return text
    if _sensitive_filter is not None:
        masked, _ = _sensitive_filter.mask(text)
        return masked
    # Fallback: simple built-in patterns if SensitiveFilter import failed
    for pattern in (
        r'sk-[a-zA-Z0-9]{20,}',
        r'AIza[a-zA-Z0-9\-_]{35}',
        r'(?i)password\s*[=:]\s*\S+',
    ):
        text = re.sub(pattern, "[REDACTED]", text, flags=re.IGNORECASE)
    return text


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

_INSERT_SQL = """
INSERT OR IGNORE INTO ai_prompts
    (timestamp, tool, prompt_text, response_text, session_id, data)
VALUES
    (:timestamp, :tool, :prompt_text, :response_text, :session_id, :data)
"""


def save_session(payload: dict) -> int:
    """Insert ai_prompts record. Returns new row id or 0."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    try:
        conn = sqlite3.connect(DB_PATH)
        # Ensure column exists (server may run before init_db)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_prompts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                tool TEXT,
                project TEXT,
                prompt_text TEXT,
                response_text TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                session_id TEXT,
                data TEXT,
                UNIQUE(session_id, timestamp)
            )
        """)
        ts = payload.get("timestamp") or datetime.now().isoformat()
        record = {
            "timestamp": ts,
            "tool": payload.get("tool", "unknown"),
            "prompt_text": mask_sensitive(payload.get("prompt_text", "")),
            "response_text": mask_sensitive(payload.get("response_text", "")),
            "session_id": payload.get("session_id") or f"{payload.get('tool','x')}_{ts}",
            "data": json.dumps({
                "url": payload.get("url", ""),
                "project": payload.get("project", ""),
            }),
        }
        with conn:
            cur = conn.execute(_INSERT_SQL, record)
            row_id = cur.lastrowid or 0
        conn.close()
        return row_id
    except sqlite3.Error as e:
        log.error("DB error: %s", e)
        return 0


def get_today_count() -> int:
    try:
        conn = sqlite3.connect(DB_PATH)
        today = datetime.now().strftime("%Y-%m-%d")
        (count,) = conn.execute(
            "SELECT COUNT(*) FROM ai_prompts WHERE timestamp LIKE ?", (f"{today}%",)
        ).fetchone()
        conn.close()
        return count
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class AISessionHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):  # noqa: A002
        log.info("HTTP %s", format % args)

    def _send_json(self, code: int, body: dict):
        data = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        # CORS for browser extension
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):  # preflight
        self._send_json(200, {})

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {"status": "ok", "version": VERSION})
        elif self.path == "/status":
            self._send_json(200, {
                "status": "ok",
                "today_sessions": get_today_count(),
                "dry_run": _DRY_RUN,
            })
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        # localhost only
        client_ip = self.client_address[0]
        if client_ip not in ("127.0.0.1", "::1"):
            self._send_json(403, {"error": "forbidden"})
            return

        if self.path != "/ai-session":
            self._send_json(404, {"error": "not found"})
            return

        content_type = self.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            self._send_json(400, {"error": "Content-Type must be application/json"})
            return

        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            self._send_json(400, {"error": "empty body"})
            return

        try:
            body = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as e:
            self._send_json(400, {"error": f"invalid JSON: {e}"})
            return

        if not body.get("prompt_text"):
            self._send_json(400, {"error": "prompt_text is required"})
            return

        if _DRY_RUN:
            tool = body.get("tool", "?")
            preview = body.get("prompt_text", "")[:80].replace("\n", " ")
            log.info("[DRY-RUN] %s: %s", tool, preview)
            self._send_json(200, {"status": "ok", "id": 0, "dry_run": True})
            return

        row_id = save_session(body)
        if row_id:
            log.info("Saved ai_prompt id=%d tool=%s", row_id, body.get("tool"))
            self._send_json(200, {"status": "ok", "id": row_id})
        else:
            self._send_json(200, {"status": "ok", "id": 0, "note": "duplicate or error"})


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

def start_server(port: int = 7331, dry_run: bool = False) -> HTTPServer:
    global _DRY_RUN
    _DRY_RUN = dry_run
    server = HTTPServer(("127.0.0.1", port), AISessionHandler)
    return server


def run(port: int = 7331, dry_run: bool = False) -> None:
    server = start_server(port, dry_run)
    mode = "DRY-RUN" if dry_run else "WRITE"
    log.info("DayTracker server [%s] listening on http://127.0.0.1:%d", mode, port)
    log.info("Endpoints: GET /health  GET /status  POST /ai-session")
    log.info("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Server stopped.")
    finally:
        server.server_close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="DayTracker local receiver server")
    parser.add_argument("--port", type=int, default=7331)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(port=args.port, dry_run=args.dry_run)
