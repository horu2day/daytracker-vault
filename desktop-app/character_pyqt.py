"""
desktop-app/character_pyqt.py - DayTracker Phase 7 Desktop Character Agent.

A transparent, always-on-top desktop companion that shows DayTracker status
using PyQt6. The character sits in the bottom-right corner of the screen and
animates based on the user's work state (idle, working, sleeping, alert).

Features:
  - Click character  -> show today's status summary
  - Right-click      -> show morning briefing (compact)
  - Stuck-file hints surfaced automatically every 5 minutes
  - Drag to reposition
  - Tray icon with quit option

Requirements:
    pip install PyQt6

Usage:
    python desktop-app/character_pyqt.py [--project-root PATH]
    # Or let it auto-detect from this file's location.

State machine:
    idle      -> bouncing gently (default)
    working   -> rapid wobble (recent file event < 5 min)
    sleeping  -> slow sway + dim (no activity > 30 min)
    alert     -> excited bounce + bubble (notification pending)
    celebrate -> spin once (milestone: e.g. 50th AI session today)
"""

from __future__ import annotations

import argparse
import io
import os
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Windows UTF-8 stdout
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    if hasattr(sys.stdout, "buffer") and not getattr(sys.stdout, "_daytracker_wrapped", False):
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
        )
        sys.stdout._daytracker_wrapped = True  # type: ignore[attr-defined]
    if hasattr(sys.stderr, "buffer") and not getattr(sys.stderr, "_daytracker_wrapped", False):
        sys.stderr = io.TextIOWrapper(
            sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True
        )
        sys.stderr._daytracker_wrapped = True  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Auto-detect project root
# ---------------------------------------------------------------------------
_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent  # desktop-app/../ = daytracker-vault/

def _find_project_root(override: Optional[str] = None) -> Path:
    if override:
        return Path(override).resolve()
    # Walk up looking for CLAUDE.md or scripts/ directory
    candidate = _THIS_DIR.parent
    while candidate != candidate.parent:
        if (candidate / "scripts").exists() and (candidate / "scripts" / "config.py").exists():
            return candidate
        candidate = candidate.parent
    return _THIS_DIR.parent  # fallback


# ---------------------------------------------------------------------------
# PyQt6 import with clear error message
# ---------------------------------------------------------------------------
try:
    from PyQt6.QtWidgets import (
        QApplication,
        QLabel,
        QWidget,
        QSystemTrayIcon,
        QMenu,
        QSizePolicy,
    )
    from PyQt6.QtCore import (
        Qt,
        QTimer,
        QPoint,
        QPropertyAnimation,
        QRect,
        QSequentialAnimationGroup,
        QAbstractAnimation,
        pyqtSignal,
        QObject,
        QThread,
    )
    from PyQt6.QtGui import (
        QFont,
        QColor,
        QPainter,
        QPainterPath,
        QIcon,
        QPixmap,
        QAction,
        QCursor,
    )
    _PYQT6_AVAILABLE = True
except ImportError:
    _PYQT6_AVAILABLE = False
    print(
        "[DayTracker] PyQt6 is not installed.\n"
        "  Install it with: pip install PyQt6\n"
        "  Then re-run: python desktop-app/character_pyqt.py",
        file=sys.stderr,
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHAR_SIZE = 80          # pixels, character emoji label size
BUBBLE_WIDTH = 290      # speech bubble max width
BUBBLE_MAX_LINES = 10   # lines shown in bubble
TICK_INTERVAL_MS = 60_000   # activity check every 60 s
BUBBLE_DISPLAY_MS = 7_000   # auto-hide bubble after N ms
STUCK_CHECK_INTERVAL_MS = 5 * 60_000   # stuck detector check every 5 min

# Character state -> emoji
CHAR_EMOJI: dict[str, str] = {
    "idle":      "🐶",
    "working":   "🐕",
    "sleeping":  "🐾",
    "alert":     "🐩",
    "celebrate": "🦴",
}

# Colours
BUBBLE_BG   = QColor(30, 30, 40, 230)
BUBBLE_TEXT = QColor(224, 224, 224)
BUBBLE_BORDER = QColor(255, 255, 255, 26)


# ---------------------------------------------------------------------------
# Background worker: calls Python scripts, returns text via signal
# ---------------------------------------------------------------------------

class ScriptWorker(QObject):
    """Runs a subprocess in a background thread and emits the result."""
    finished = pyqtSignal(str, str)   # (tag, output_text)

    def __init__(self, tag: str, cmd: list[str], cwd: str) -> None:
        super().__init__()
        self._tag = tag
        self._cmd = cmd
        self._cwd = cwd

    def run(self) -> None:
        try:
            result = subprocess.run(
                self._cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                cwd=self._cwd,
            )
            output = result.stdout.strip() or result.stderr.strip()
        except subprocess.TimeoutExpired:
            output = "(timeout)"
        except Exception as exc:
            output = f"(error: {exc})"
        self.finished.emit(self._tag, output)


# ---------------------------------------------------------------------------
# Speech bubble widget
# ---------------------------------------------------------------------------

class BubbleWidget(QWidget):
    """A rounded speech bubble with an arrow pointing down-right."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._text = ""
        self._label = QLabel(self)
        self._label.setWordWrap(True)
        self._label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        font = QFont("Segoe UI", 10)
        self._label.setFont(font)
        self._label.setStyleSheet(f"color: rgb(224,224,224); background: transparent; padding: 2px;")
        self._label.setMaximumWidth(BUBBLE_WIDTH - 28)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def show_text(self, text: str, duration_ms: int = BUBBLE_DISPLAY_MS) -> None:
        """Set the bubble text and show for duration_ms milliseconds."""
        # Trim to max lines
        lines = text.splitlines()
        if len(lines) > BUBBLE_MAX_LINES:
            lines = lines[:BUBBLE_MAX_LINES] + ["..."]
        self._text = "\n".join(lines)
        self._label.setText(self._text)
        self._label.adjustSize()

        # Resize widget to fit text + padding
        lw = self._label.sizeHint().width() + 28
        lh = self._label.sizeHint().height() + 28
        bubble_w = max(160, min(BUBBLE_WIDTH, lw))
        bubble_h = max(40, lh) + 12  # +12 for arrow
        self.resize(bubble_w, bubble_h)
        self._label.setGeometry(12, 10, bubble_w - 24, bubble_h - 22)

        self.show()
        self.raise_()
        if duration_ms > 0:
            self._hide_timer.start(duration_ms)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height() - 12  # leave room for arrow

        # Background rounded rect
        path = QPainterPath()
        path.addRoundedRect(0, 0, w, h, 10, 10)
        painter.fillPath(path, BUBBLE_BG)

        # Border
        painter.setPen(BUBBLE_BORDER)
        painter.drawPath(path)

        # Arrow (pointing down, positioned bottom-right)
        arrow_x = w - 36
        arrow_y = h
        arrow_pts = [
            QPoint(arrow_x, arrow_y),
            QPoint(arrow_x + 16, arrow_y),
            QPoint(arrow_x + 8, arrow_y + 12),
        ]
        from PyQt6.QtGui import QPolygon
        poly = QPolygon(arrow_pts)
        painter.setBrush(BUBBLE_BG)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(poly)

        painter.end()


# ---------------------------------------------------------------------------
# Main character window
# ---------------------------------------------------------------------------

class CharacterWindow(QWidget):
    """
    The main transparent, always-on-top character widget.

    Layout (virtual, no decorations):
      +-----+
      | 🤖  |  <-- CHAR_SIZE x CHAR_SIZE emoji label
      +-----+
    """

    def __init__(self, project_root: str) -> None:
        super().__init__()
        self._project_root = project_root
        self._db_path = str(Path(project_root) / "data" / "worklog.db")
        self._state = "idle"
        self._dragging = False
        self._drag_pos = QPoint()
        self._bubble_visible = False

        # ── Window flags ────────────────────────────────────────────────────
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(CHAR_SIZE, CHAR_SIZE)

        # Position: bottom-right corner
        screen = QApplication.primaryScreen()
        if screen:
            sg = screen.availableGeometry()
            self.move(sg.right() - CHAR_SIZE - 20, sg.bottom() - CHAR_SIZE - 20)

        # ── Character emoji label ────────────────────────────────────────────
        self._char_label = QLabel(CHAR_EMOJI["idle"], self)
        self._char_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._char_label.setGeometry(0, 0, CHAR_SIZE, CHAR_SIZE)
        font = QFont("Segoe UI Emoji", 44)
        self._char_label.setFont(font)
        self._char_label.setStyleSheet("background: transparent;")
        self._char_label.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        # ── Speech bubble (separate top-level widget) ────────────────────────
        self._bubble = BubbleWidget()

        # ── Animation timer ──────────────────────────────────────────────────
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._animate_tick)
        self._anim_step = 0
        self._anim_timer.start(80)  # ~12 fps

        # ── Activity check timer ─────────────────────────────────────────────
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._do_activity_check)
        self._tick_timer.start(TICK_INTERVAL_MS)

        # ── Stuck detector timer ─────────────────────────────────────────────
        self._stuck_timer = QTimer(self)
        self._stuck_timer.timeout.connect(self._check_stuck)
        self._stuck_timer.start(STUCK_CHECK_INTERVAL_MS)

        # ── Background worker thread ─────────────────────────────────────────
        self._worker_thread: Optional[QThread] = None
        self._worker: Optional[ScriptWorker] = None

        # ── System tray ──────────────────────────────────────────────────────
        self._setup_tray()

        # Run initial check after 3 s
        QTimer.singleShot(3000, self._do_activity_check)

    # ------------------------------------------------------------------
    # Tray setup
    # ------------------------------------------------------------------

    def _setup_tray(self) -> None:
        """Create a system tray icon with a quit action."""
        # Build a simple 16x16 pixmap for tray icon (dog emoji rendered to image)
        pix = QPixmap(16, 16)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setFont(QFont("Segoe UI Emoji", 10))
        p.drawText(0, 12, "🐶")
        p.end()

        self._tray = QSystemTrayIcon(QIcon(pix), self)
        self._tray.setToolTip("🐶 DayTracker 강아지 에이전트")

        menu = QMenu()
        status_action = QAction("오늘 상태 보기", self)
        status_action.triggered.connect(self._on_left_click)
        briefing_action = QAction("아침 브리핑", self)
        briefing_action.triggered.connect(self._on_right_click)
        quit_action = QAction("종료", self)
        quit_action.triggered.connect(QApplication.instance().quit)

        menu.addAction(status_action)
        menu.addAction(briefing_action)
        menu.addSeparator()
        menu.addAction(quit_action)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            # Single click on tray icon -> show/hide main window
            if self.isVisible():
                self.hide()
            else:
                self.show()
                self.raise_()

    # ------------------------------------------------------------------
    # Mouse events (drag + click)
    # ------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            self._on_right_click()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._dragging and event.buttons() & Qt.MouseButton.LeftButton:
            new_pos = event.globalPosition().toPoint() - self._drag_pos
            self.move(new_pos)
            self._reposition_bubble()
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self._drag_pos - self.frameGeometry().topLeft()
            if delta.manhattanLength() < 6:
                # It was a click, not a drag
                self._on_left_click()
            self._dragging = False
            event.accept()

    # ------------------------------------------------------------------
    # Bubble positioning
    # ------------------------------------------------------------------

    def _reposition_bubble(self) -> None:
        """Move bubble to sit just above and to the left of the character."""
        char_global = self.mapToGlobal(QPoint(0, 0))
        bw = self._bubble.width() or BUBBLE_WIDTH
        bh = self._bubble.height() or 80
        bx = char_global.x() + CHAR_SIZE - bw
        by = char_global.y() - bh - 4
        # Keep on screen
        screen = QApplication.primaryScreen()
        if screen:
            sg = screen.availableGeometry()
            bx = max(sg.left(), min(bx, sg.right() - bw))
            by = max(sg.top(), by)
        self._bubble.move(bx, by)

    def _show_bubble(self, text: str, duration_ms: int = BUBBLE_DISPLAY_MS) -> None:
        self._bubble.show_text(text, duration_ms)
        self._reposition_bubble()

    def _hide_bubble(self) -> None:
        self._bubble.hide()

    # ------------------------------------------------------------------
    # Animation
    # ------------------------------------------------------------------

    def _animate_tick(self) -> None:
        """Called ~12x/sec to drive CSS-like animations via offset."""
        self._anim_step = (self._anim_step + 1) % 360
        step = self._anim_step

        import math
        if self._state == "idle":
            # Gentle float: sine wave ±4px vertically
            dy = int(4 * math.sin(math.radians(step * 2)))
            self._char_label.setGeometry(0, dy, CHAR_SIZE, CHAR_SIZE)

        elif self._state == "working":
            # Fast wobble: alternating ±4px horizontally
            dx = int(4 * math.sin(math.radians(step * 8)))
            self._char_label.setGeometry(dx, 0, CHAR_SIZE, CHAR_SIZE)

        elif self._state == "sleeping":
            # Slow sway + slight dim
            dy = int(3 * math.sin(math.radians(step)))
            opacity = 0.6 + 0.3 * math.sin(math.radians(step * 0.5))
            self.setWindowOpacity(max(0.5, min(1.0, opacity)))
            self._char_label.setGeometry(0, dy, CHAR_SIZE, CHAR_SIZE)

        elif self._state == "alert":
            # Excited bounce: bigger sine
            dy = int(8 * abs(math.sin(math.radians(step * 4))))
            self._char_label.setGeometry(0, -dy, CHAR_SIZE, CHAR_SIZE)

        elif self._state == "celebrate":
            # Spin-like shimmy
            dx = int(6 * math.sin(math.radians(step * 6)))
            dy = int(4 * math.cos(math.radians(step * 6)))
            self._char_label.setGeometry(dx, dy, CHAR_SIZE, CHAR_SIZE)

        # Restore opacity when not sleeping
        if self._state != "sleeping":
            self.setWindowOpacity(1.0)

    def _set_state(self, new_state: str) -> None:
        if new_state == self._state:
            return
        self._state = new_state
        emoji = CHAR_EMOJI.get(new_state, CHAR_EMOJI["idle"])
        self._char_label.setText(emoji)

    # ------------------------------------------------------------------
    # Click handlers
    # ------------------------------------------------------------------

    def _on_left_click(self) -> None:
        """Left click: toggle today's status bubble."""
        if self._bubble.isVisible():
            self._hide_bubble()
            return
        self._set_state("alert")
        self._run_script(
            tag="status",
            cmd=[
                sys.executable,
                "scripts/agents/morning_briefing.py",
                "--dry-run",
                "--short",
            ],
        )

    def _on_right_click(self) -> None:
        """Right click: show full morning briefing (dry-run)."""
        if self._bubble.isVisible():
            self._hide_bubble()
        self._set_state("alert")
        self._run_script(
            tag="briefing",
            cmd=[
                sys.executable,
                "scripts/agents/morning_briefing.py",
                "--dry-run",
            ],
        )

    # ------------------------------------------------------------------
    # Periodic activity check
    # ------------------------------------------------------------------

    def _do_activity_check(self) -> None:
        """Query worklog.db for last activity age and update state."""
        minutes = self._get_last_activity_minutes()
        if minutes is None:
            return

        if minutes > 30:
            self._set_state("sleeping")
        elif minutes < 5:
            self._set_state("working")
            # Revert to idle after 5 s
            QTimer.singleShot(5000, lambda: self._set_state("idle"))
        else:
            if self._state not in ("alert", "celebrate"):
                self._set_state("idle")

    def _get_last_activity_minutes(self) -> Optional[int]:
        """Return minutes since last file_event, or None if DB unavailable."""
        db = Path(self._db_path)
        if not db.exists():
            return None
        try:
            with sqlite3.connect(str(db), timeout=3) as conn:
                row = conn.execute(
                    "SELECT MAX(timestamp) FROM file_events"
                ).fetchone()
            if row and row[0]:
                last_str = row[0].replace("Z", "+00:00")
                last_dt = datetime.fromisoformat(last_str)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                now_utc = datetime.now(timezone.utc)
                elapsed = (now_utc - last_dt).total_seconds() / 60
                return max(0, int(elapsed))
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Stuck detector check
    # ------------------------------------------------------------------

    def _check_stuck(self) -> None:
        """Run stuck detector in background, show bubble if stuck detected."""
        self._run_script(
            tag="stuck",
            cmd=[
                sys.executable,
                "scripts/agents/stuck_detector.py",
                "--short",
                "--threshold-minutes", "30",
            ],
        )

    # ------------------------------------------------------------------
    # Background script runner
    # ------------------------------------------------------------------

    def _run_script(self, tag: str, cmd: list[str]) -> None:
        """Run cmd in a background QThread; on finish call _on_script_done."""
        # Cancel previous worker if still running
        if self._worker_thread and self._worker_thread.isRunning():
            return  # busy; skip this request

        self._worker = ScriptWorker(tag, cmd, self._project_root)
        self._worker_thread = QThread()
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_script_done)
        self._worker.finished.connect(self._worker_thread.quit)
        self._worker_thread.start()

    def _on_script_done(self, tag: str, text: str) -> None:
        """Handle output from a finished background script."""
        text = text.strip()

        if tag == "status":
            if text:
                self._show_bubble(text, BUBBLE_DISPLAY_MS)
            else:
                self._show_bubble("🐶 왈왈! 데이터 없음\n(watcher_daemon.py 실행 필요)", 5000)
            QTimer.singleShot(2500, lambda: self._set_state("idle"))

        elif tag == "briefing":
            if text:
                self._show_bubble(text, 12_000)
            else:
                self._show_bubble("🐶 아직 데이터가 없어요!", 4000)
            QTimer.singleShot(3000, lambda: self._set_state("idle"))

        elif tag == "stuck":
            if text:
                # A non-empty result means the user is stuck
                self._set_state("alert")
                self._show_bubble(f"🐶 왈왈! 혹시 막히셨나요?\n{text}", 8000)
                QTimer.singleShot(8500, lambda: self._set_state("idle"))
            # If empty: not stuck; stay in current state

    # ------------------------------------------------------------------
    # Today's status from DB directly (fast, no subprocess)
    # ------------------------------------------------------------------

    def _get_today_status_direct(self) -> str:
        """Query today's stats from DB and return a short summary string."""
        db = Path(self._db_path)
        if not db.exists():
            return "DB 없음 - python scripts/init_db.py 실행 필요"

        today = datetime.now().strftime("%Y-%m-%d")
        try:
            with sqlite3.connect(str(db), timeout=3) as conn:
                ai_count = conn.execute(
                    "SELECT COUNT(*) FROM ai_prompts WHERE timestamp LIKE ?",
                    (f"{today}%",),
                ).fetchone()[0]
                file_count = conn.execute(
                    "SELECT COUNT(*) FROM file_events WHERE timestamp LIKE ?",
                    (f"{today}%",),
                ).fetchone()[0]
                project_rows = conn.execute(
                    """
                    SELECT COALESCE(p.name, 'unknown') AS pname, COUNT(*) AS cnt
                    FROM file_events fe
                    LEFT JOIN projects p ON fe.project_id = p.id
                    WHERE fe.timestamp LIKE ?
                    GROUP BY pname
                    ORDER BY cnt DESC
                    LIMIT 3
                    """,
                    (f"{today}%",),
                ).fetchall()

            parts = []
            for name, cnt in project_rows:
                parts.append(f"{name}({cnt}개)")
            proj_str = " | ".join(parts) if parts else "활동 없음"

            return (
                f"🐶 오늘 {today}\n"
                f"AI 세션: {ai_count}건  |  파일: {file_count}건\n"
                f"프로젝: {proj_str}"
            )
        except Exception as exc:
            return f"DB 쿼리 오류: {exc}"


# ---------------------------------------------------------------------------
# Application entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="DayTracker Desktop Character Agent (PyQt6)"
    )
    parser.add_argument(
        "--project-root",
        default="",
        help=(
            "Absolute path to the daytracker-vault project root. "
            "Auto-detected if not provided."
        ),
    )
    args = parser.parse_args(argv)

    project_root = str(_find_project_root(args.project_root or None))
    print(f"[character] Project root: {project_root}")

    # Verify DB exists (warn but don't abort)
    db_path = Path(project_root) / "data" / "worklog.db"
    if not db_path.exists():
        print(
            f"[character] WARNING: worklog.db not found at {db_path}\n"
            "  Run: python scripts/init_db.py\n"
            "  The character will still run but status info will be empty.",
            file=sys.stderr,
        )

    app = QApplication(sys.argv if argv is None else [sys.argv[0]] + argv)
    app.setQuitOnLastWindowClosed(False)  # keep alive via tray
    app.setApplicationName("DayTracker")

    window = CharacterWindow(project_root)
    window.show()

    print("[character] DayTracker character agent started.")
    print("  Left-click  -> today's status")
    print("  Right-click -> morning briefing")
    print("  Drag        -> reposition")
    print("  Tray icon   -> hide/show / quit")

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
