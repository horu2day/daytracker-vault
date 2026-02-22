"""
desktop-app/character_lottie.py - DayTracker 강아지 캐릭터 (Lottie 애니메이션 버전)

Lottie JSON 파일을 rlottie-python으로 렌더링하여 PyQt6 창에 표시.
상태에 따라 재생 속도, 크기, 투명도를 다르게 적용.

Requirements:
    pip install PyQt6 rlottie-python Pillow

Usage:
    python desktop-app/character_lottie.py

Lottie 파일 위치:
    data/Puppy sleeping.json   - sleeping 애니메이션 (현재)
    data/puppy_idle.json       - (추가 예정)
    data/puppy_working.json    - (추가 예정)
    data/puppy_alert.json      - (추가 예정)
"""

from __future__ import annotations

import io
import os
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Windows UTF-8
if sys.platform == "win32":
    if hasattr(sys.stdout, "buffer") and not getattr(sys.stdout, "_daytracker_wrapped", False):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
        sys.stdout._daytracker_wrapped = True  # type: ignore
    if hasattr(sys.stderr, "buffer") and not getattr(sys.stderr, "_daytracker_wrapped", False):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr._daytracker_wrapped = True  # type: ignore

_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent

try:
    from PyQt6.QtWidgets import QApplication, QWidget, QLabel, QSystemTrayIcon, QMenu
    from PyQt6.QtCore import Qt, QTimer, QSize, QPoint
    from PyQt6.QtGui import QPixmap, QImage, QFont, QIcon, QPainter, QColor, QPainterPath, QAction
except ImportError:
    print("PyQt6 not found. Run: pip install PyQt6")
    sys.exit(1)

try:
    import rlottie_python as rl
    LOTTIE_AVAILABLE = True
except ImportError:
    print("rlottie-python not found. Run: pip install rlottie-python")
    LOTTIE_AVAILABLE = False
    sys.exit(1)

try:
    from PIL.Image import Image as PILImage
except ImportError:
    print("Pillow not found. Run: pip install Pillow")
    sys.exit(1)

# ---------------------------------------------------------------------------
# 경로 설정
# ---------------------------------------------------------------------------
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))

DATA_DIR = _PROJECT_ROOT / "data"

# Lottie 파일 맵 — 파일이 없으면 sleeping으로 폴백
LOTTIE_FILES = {
    "idle":      DATA_DIR / "puppy_idle.json",
    "working":   DATA_DIR / "puppy_working.json",
    "sleeping":  DATA_DIR / "Puppy sleeping.json",
    "alert":     DATA_DIR / "puppy_alert.json",
    "celebrate": DATA_DIR / "puppy_celebrate.json",
}
FALLBACK_LOTTIE = DATA_DIR / "Puppy sleeping.json"

# 상태별 렌더링 설정
STATE_CONFIG = {
    "idle":      {"size": 160, "speed": 1.0,  "opacity": 1.0},
    "working":   {"size": 160, "speed": 2.5,  "opacity": 1.0},   # 빠르게 재생
    "sleeping":  {"size": 140, "speed": 0.6,  "opacity": 0.75},  # 느리고 반투명
    "alert":     {"size": 180, "speed": 1.8,  "opacity": 1.0},   # 크게, 약간 빠르게
    "celebrate": {"size": 190, "speed": 2.0,  "opacity": 1.0},
}

TICK_INTERVAL_MS = 60_000       # 활동 체크 1분
STUCK_CHECK_MS   = 5 * 60_000  # stuck 체크 5분
BUBBLE_MS        = 7_000        # 말풍선 자동 숨김


# ---------------------------------------------------------------------------
# Lottie 애니메이션 로더
# ---------------------------------------------------------------------------
class LottiePlayer:
    """단일 Lottie 파일 → PyQt6 QPixmap 스트림."""

    def __init__(self, json_path: Path, render_size: int = 160):
        self._path = json_path
        self._size = render_size
        self._anim: Optional[rl.LottieAnimation] = None
        self._total_frames = 0
        self._frame_idx = 0
        self._speed = 1.0
        self._load()

    def _load(self):
        try:
            self._anim = rl.LottieAnimation.from_file(str(self._path))
            self._total_frames = self._anim.lottie_animation_get_totalframe()
        except Exception as e:
            print(f"[LottiePlayer] Failed to load {self._path}: {e}", file=sys.stderr)
            self._anim = None
            self._total_frames = 0

    def set_speed(self, speed: float):
        self._speed = max(0.1, speed)

    def set_size(self, size: int):
        self._size = size

    def next_frame(self) -> Optional[QPixmap]:
        if self._anim is None or self._total_frames == 0:
            return None
        try:
            # render_pillow_frame에 크기 인자를 넘기면 Segfault 발생
            # → 기본 크기로 렌더링 후 PIL로 리사이즈
            pil_img = self._anim.render_pillow_frame(
                int(self._frame_idx) % self._total_frames
            )
            if pil_img.size != (self._size, self._size):
                pil_img = pil_img.resize((self._size, self._size))
            self._frame_idx = (self._frame_idx + self._speed) % self._total_frames
            return self._pil_to_pixmap(pil_img)
        except Exception:
            return None

    @staticmethod
    def _pil_to_pixmap(pil_img) -> QPixmap:
        from PIL import Image, ImageDraw
        # 어두운 바탕에서도 보이도록: 완전 투명 픽셀이 지배적이면
        # 연한 원형 배경을 합성한다.
        px = list(pil_img.getdata())
        non_trans = sum(1 for p in px if p[3] > 10)
        total = len(px)
        if non_trans < total * 0.15:
            # 배경이 거의 없는 파일 → 크림색 원 추가
            w, h = pil_img.size
            bg = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(bg)
            r = int(min(w, h) * 0.44)
            cx, cy = w // 2, h // 2
            draw.ellipse(
                [cx - r, cy - r, cx + r, cy + r],
                fill=(250, 245, 225, 230),  # 크림색 92% 불투명
            )
            pil_img = Image.alpha_composite(bg, pil_img.convert("RGBA"))

        data = pil_img.tobytes("raw", "RGBA")
        qimg = QImage(data, pil_img.width, pil_img.height, QImage.Format.Format_RGBA8888)
        return QPixmap.fromImage(qimg)


# ---------------------------------------------------------------------------
# 말풍선 위젯
# ---------------------------------------------------------------------------
class BubbleWidget(QWidget):
    BG    = QColor(28, 28, 38, 230)
    TEXT  = QColor(230, 230, 230)
    BORDER = QColor(255, 255, 255, 30)
    RADIUS = 14

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Tool |
                         Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, False)
        self._label = QLabel(self)
        self._label.setWordWrap(True)
        self._label.setMaximumWidth(270)
        self._label.setStyleSheet("color: #e6e6e6; font-size: 13px; padding: 4px;")
        self._label.setFont(QFont("Malgun Gothic", 11))
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_text(self, text: str, duration_ms: int = BUBBLE_MS):
        self._label.setText(text)
        self._label.adjustSize()
        w = self._label.width() + 28
        h = self._label.height() + 28
        self.resize(w, h)
        self._label.move(14, 14)
        self._timer.start(duration_ms)
        self.show()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), self.RADIUS, self.RADIUS)
        p.fillPath(path, self.BG)
        p.setPen(self.BORDER)
        p.drawPath(path)


# ---------------------------------------------------------------------------
# 백그라운드 스크립트 실행 (QThread 없이 threading 사용)
# ---------------------------------------------------------------------------
class ScriptWorker:
    def __init__(self, on_done):
        self._on_done = on_done
        self._running = False

    def run(self, tag: str, cmd: list[str]):
        if self._running:
            return
        self._running = True
        def _work():
            try:
                r = subprocess.run(cmd, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace",
                                   cwd=str(_PROJECT_ROOT), timeout=20)
                self._on_done(tag, r.stdout.strip())
            except Exception as e:
                self._on_done(tag, "")
            finally:
                self._running = False
        threading.Thread(target=_work, daemon=True).start()


# ---------------------------------------------------------------------------
# 메인 캐릭터 창
# ---------------------------------------------------------------------------
class DogCharacter(QWidget):

    def __init__(self):
        super().__init__()
        self._state = "sleeping"
        self._drag_pos: Optional[QPoint] = None
        self._bubble = BubbleWidget()
        self._worker = ScriptWorker(self._on_script_done)

        # Lottie 플레이어 캐시 {상태: LottiePlayer}
        self._players: dict[str, LottiePlayer] = {}
        self._current_player: Optional[LottiePlayer] = None
        self._load_players()

        self._setup_window()
        self._setup_tray()
        self._setup_timers()
        self._set_state("sleeping")

    # ------------------------------------------------------------------
    # 초기화
    # ------------------------------------------------------------------
    def _load_players(self):
        """상태별 LottiePlayer 로드. 파일 없으면 fallback."""
        for state, cfg in STATE_CONFIG.items():
            path = LOTTIE_FILES.get(state, FALLBACK_LOTTIE)
            if not path.exists():
                path = FALLBACK_LOTTIE
            p = LottiePlayer(path, cfg["size"])
            p.set_speed(cfg["speed"])
            self._players[state] = p

    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("DayTracker 강아지")

        # 화면 우하단 배치
        screen = QApplication.primaryScreen().availableGeometry()
        size = STATE_CONFIG["sleeping"]["size"]
        self.resize(size + 20, size + 20)
        self.move(screen.right() - self.width() - 20,
                  screen.bottom() - self.height() - 20)

        # 렌더링 레이블
        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.resize(self.width(), self.height())
        self.show()

    def _setup_tray(self):
        pix = QPixmap(16, 16)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setFont(QFont("Segoe UI Emoji", 10))
        p.drawText(0, 12, "🐶")
        p.end()

        self._tray = QSystemTrayIcon(QIcon(pix), self)
        self._tray.setToolTip("🐶 DayTracker 강아지")
        menu = QMenu()
        menu.addAction(QAction("오늘 상태", self, triggered=self._on_click_status))
        menu.addAction(QAction("아침 브리핑", self, triggered=self._on_click_briefing))
        menu.addSeparator()
        menu.addAction(QAction("종료", self, triggered=QApplication.quit))
        self._tray.setContextMenu(menu)
        self._tray.show()

    def _setup_timers(self):
        # 애니메이션 프레임 타이머 (~30fps)
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._next_frame)
        self._anim_timer.start(33)

        # 활동 상태 체크
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start(TICK_INTERVAL_MS)

        # Stuck 감지
        self._stuck_timer = QTimer(self)
        self._stuck_timer.timeout.connect(self._check_stuck)
        self._stuck_timer.start(STUCK_CHECK_MS)

    # ------------------------------------------------------------------
    # 상태 관리
    # ------------------------------------------------------------------
    def _set_state(self, state: str):
        if state not in STATE_CONFIG:
            state = "sleeping"
        self._state = state
        cfg = STATE_CONFIG[state]

        self._current_player = self._players.get(state)
        if self._current_player:
            self._current_player.set_speed(cfg["speed"])
            self._current_player.set_size(cfg["size"])
            self._current_player._frame_idx = 0  # 처음부터 재생

        # 창 크기 조정
        new_size = cfg["size"] + 20
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(new_size, new_size)
        self._label.resize(new_size, new_size)
        self.move(screen.right() - new_size - 20,
                  screen.bottom() - new_size - 20)

        # 투명도
        self.setWindowOpacity(cfg["opacity"])

        # 버블 위치 업데이트
        self._reposition_bubble()

    # ------------------------------------------------------------------
    # 애니메이션 루프
    # ------------------------------------------------------------------
    def _next_frame(self):
        if self._current_player is None:
            return
        pix = self._current_player.next_frame()
        if pix:
            self._label.setPixmap(pix)

    # ------------------------------------------------------------------
    # 말풍선
    # ------------------------------------------------------------------
    def _reposition_bubble(self):
        gpos = self.mapToGlobal(QPoint(0, 0))
        bw = self._bubble.width() if self._bubble.width() > 50 else 200
        bh = self._bubble.height() if self._bubble.height() > 20 else 60
        self._bubble.move(gpos.x() - bw + self.width(),
                          gpos.y() - bh - 10)

    def _show_bubble(self, text: str, duration_ms: int = BUBBLE_MS):
        self._reposition_bubble()
        self._bubble.show_text(text, duration_ms)

    def _hide_bubble(self):
        self._bubble.hide()

    # ------------------------------------------------------------------
    # 클릭 / 드래그
    # ------------------------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag_pos and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
            self._reposition_bubble()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            if self._drag_pos:
                moved = (e.globalPosition().toPoint() - self.frameGeometry().topLeft() - self._drag_pos).manhattanLength()
                if moved < 6:
                    self._on_click_status()
            self._drag_pos = None
        elif e.button() == Qt.MouseButton.RightButton:
            self._on_click_briefing()

    # ------------------------------------------------------------------
    # 액션
    # ------------------------------------------------------------------
    def _on_click_status(self):
        if self._bubble.isVisible():
            self._hide_bubble()
            return
        # DB에서 직접 오늘 상태 조회
        text = self._query_today_status()
        self._show_bubble(text, 8000)
        self._set_state("alert")
        QTimer.singleShot(3000, lambda: self._set_state("idle"))

    def _on_click_briefing(self):
        script = _PROJECT_ROOT / "scripts" / "agents" / "morning_briefing.py"
        if script.exists():
            self._set_state("alert")
            self._show_bubble("브리핑 불러오는 중...", 2000)
            self._worker.run("briefing", [sys.executable, str(script), "--dry-run"])
        else:
            self._show_bubble("morning_briefing.py 없음", 4000)

    def _on_script_done(self, tag: str, text: str):
        if tag == "briefing":
            if text:
                self._show_bubble(text[:400], 12_000)
            else:
                self._show_bubble("🐶 아직 데이터가 없어요!", 4000)
            QTimer.singleShot(3000, lambda: self._set_state("sleeping"))

        elif tag == "stuck":
            if text.strip():
                self._set_state("alert")
                self._show_bubble(f"🐶 혹시 막히셨나요?\n{text}", 8000)
                QTimer.singleShot(9000, lambda: self._set_state("idle"))

    # ------------------------------------------------------------------
    # 정기 작업
    # ------------------------------------------------------------------
    def _tick(self):
        """1분마다 worklog.db로 활동 상태 판단."""
        minutes = self._minutes_since_last_activity()
        if minutes is None:
            return
        if minutes > 30:
            self._set_state("sleeping")
        elif minutes < 5:
            self._set_state("working")
        else:
            if self._state not in ("alert", "celebrate"):
                self._set_state("idle")

    def _check_stuck(self):
        """5분마다 stuck_detector 실행."""
        script = _PROJECT_ROOT / "scripts" / "agents" / "stuck_detector.py"
        if script.exists():
            self._worker.run("stuck", [
                sys.executable, str(script),
                "--short", "--threshold-minutes", "30"
            ])

    # ------------------------------------------------------------------
    # DB 쿼리
    # ------------------------------------------------------------------
    def _minutes_since_last_activity(self) -> Optional[int]:
        db = DATA_DIR / "worklog.db"
        if not db.exists():
            return None
        try:
            conn = sqlite3.connect(str(db))
            row = conn.execute("SELECT MAX(timestamp) FROM file_events").fetchone()
            conn.close()
            if not row or not row[0]:
                return None
            last = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
            now = datetime.now(tz=timezone.utc)
            return int((now - last).total_seconds() / 60)
        except Exception:
            return None

    def _query_today_status(self) -> str:
        db = DATA_DIR / "worklog.db"
        if not db.exists():
            return "🐶 데이터베이스 없음\nwatcher_daemon.py를 실행해 주세요!"
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            conn = sqlite3.connect(str(db))
            ai_count = conn.execute(
                "SELECT COUNT(*) FROM ai_prompts WHERE timestamp LIKE ?", (f"{today}%",)
            ).fetchone()[0]
            file_count = conn.execute(
                "SELECT COUNT(*) FROM file_events WHERE timestamp LIKE ?", (f"{today}%",)
            ).fetchone()[0]
            projects = conn.execute("""
                SELECT COALESCE(p.name, fe.file_path), COUNT(*) as c
                FROM file_events fe
                LEFT JOIN projects p ON fe.project_id = p.id
                WHERE fe.timestamp LIKE ?
                GROUP BY 1 ORDER BY c DESC LIMIT 3
            """, (f"{today}%",)).fetchall()
            conn.close()
            proj_str = " | ".join(f"{n}({c})" for n, c in projects) if projects else "활동 없음"
            return (
                f"🐶 오늘 {today}\n"
                f"AI 세션: {ai_count}건  |  파일: {file_count}건\n"
                f"프로젝트: {proj_str}"
            )
        except Exception as ex:
            return f"DB 오류: {ex}"


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------
def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    if not FALLBACK_LOTTIE.exists():
        print(f"[ERROR] Lottie 파일 없음: {FALLBACK_LOTTIE}")
        print("data/ 폴더에 'Puppy sleeping.json' 파일을 넣어주세요.")
        sys.exit(1)

    dog = DogCharacter()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
