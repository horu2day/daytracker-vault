"""
desktop-app/character_lottie.py - DayTracker 강아지 캐릭터 (Native Vector 에디션)

기존 Lottie JSON 렌더링 방식의 버그(rlottie-python 렌더링 깨짐 문제)를 완벽히 해결하기 위해,
Gemini 3.1 Pro 스타일로 PyQt6의 고품질 QPainter(Native Vector)를 사용하여
강아지의 상태별 애니메이션을 외부 라이브러리 없이 실시간으로 부드럽게 렌더링합니다.

Usage:
    python desktop-app/launch.py --lottie
    (내부적으로 Lottie 파일을 사용하지 않고 네이티브 렌더링으로 매끄럽게 교체되었습니다!)
"""
from __future__ import annotations

import io
import os
import sqlite3
import subprocess
import sys
import threading
import random
import re
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Windows UTF-8 stdout 감싸기 유지
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
    from PyQt6.QtWidgets import QApplication, QWidget, QLabel, QSystemTrayIcon, QMenu, QInputDialog, QLineEdit
    from PyQt6.QtCore import Qt, QTimer, QSize, QPoint, QRectF, QPointF, QObject, pyqtSignal
    from PyQt6.QtGui import QPixmap, QImage, QFont, QIcon, QPainter, QColor, QPainterPath, QAction
except ImportError:
    print("PyQt6 not found. Run: pip install PyQt6")
    sys.exit(1)

sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))
DATA_DIR = _PROJECT_ROOT / "data"

STATE_CONFIG = {
    "idle":      {"size": 160, "speed": 1.0,  "opacity": 1.0},
    "working":   {"size": 160, "speed": 2.5,  "opacity": 1.0},
    "sleeping":  {"size": 140, "speed": 0.6,  "opacity": 0.75},
    "alert":     {"size": 180, "speed": 1.8,  "opacity": 1.0},
    "celebrate": {"size": 190, "speed": 2.0,  "opacity": 1.0},
}

TICK_INTERVAL_MS = 60_000
STUCK_CHECK_MS   = 5 * 60_000
BUBBLE_MS        = 7_000

# ---------------------------------------------------------------------------
# Native 강아지 애니메이션 엔진 (Lottie 파일 전혀 읽지 않고 픽셀 퍼펙트 렌더링!)
# ---------------------------------------------------------------------------
def make_osc(total, frame, val1, val2):
    t = (frame / total) * math.pi * 2
    return val1 + (val2 - val1) * (math.sin(t) + 1) / 2

class NativeDogPlayer:
    def __init__(self, state: str, render_size: int = 160):
        self._state = state
        self._size = int(render_size)
        frames_map = {"idle": 90, "working": 60, "alert": 45, "celebrate": 75, "sleeping": 120}
        self._total_frames = frames_map.get(state, 60)
        self._frame_idx = 0.0
        self._speed = 1.0
        self.is_moving = False
        self.is_flipped = False

    def set_speed(self, speed: float): self._speed = max(0.1, speed)
    def set_size(self, size: int): self._size = int(size)

    def next_frame(self) -> QPixmap:
        img = QImage(self._size, self._size, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor(0, 0, 0, 0))
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        scale = self._size / 200.0
        p.scale(scale, scale)
        if self.is_flipped:
            p.translate(200, 0)
            p.scale(-1, 1)
        self._draw_dog(p, self._frame_idx)
        p.end()

        self._frame_idx = (self._frame_idx + self._speed) % self._total_frames
        return QPixmap.fromImage(img)

    def _draw_dog(self, p: QPainter, frame: float):
        p.setPen(Qt.PenStyle.NoPen)
        # 완전 귀여운 웰시코기/시바견 스타일의 SD 디포르메 컬러
        c_body   = QColor(235, 155, 52)   # 메인 컬러 (황갈색)
        c_belly  = QColor(255, 248, 235)  # 하얀 털 부분 (배, 입, 발끝)
        c_ear    = QColor(196, 118, 22)
        c_ear_in = QColor(250, 204, 213)  # 귀 안쪽 핑크
        c_nose   = QColor(48, 42, 38)     # 코/눈
        c_cheek  = QColor(250, 160, 180, 150) # 귀여운 볼터치
        c_shadow = QColor(0, 0, 0, 30)
        c_tail   = c_body
        c_tongue = QColor(255, 122, 148)
        c_alert  = QColor(242, 64, 51)
        
        jy = 0.0
        # SD 캐릭터 비율: 머리(Head)가 크고 아래에 위치, 몸(Body)은 통통하고 쪼그맣게, 다리(Legs)는 아주 짧게!
        body_y, head_y, legs_y = 110.0, 95.0, 125.0
        shadow_scale, tail_rot, ear_rot = 1.0, 0.0, 0.0
        tongue_scl, alert_scl = 1.0, 0.0
        show_tongue = False
        eye_type = "open"

        tot = self._total_frames
        
        if self._state == "idle":
            # 숨쉬기 모션 (느리고 귀엽게 통통)
            body_y = make_osc(tot, frame, 110, 113)
            head_y = make_osc(tot, frame, 95, 96)
            tail_rot = make_osc(15, frame % 15, 20, -10)
            shadow_scale = make_osc(tot, frame, 1.0, 0.95)
            if 60 <= int(frame) <= 64: eye_type = "close"

        elif self._state == "working":
            # 바쁘게 뽈뽈뽈 달리기 (다리 훨씬 더 빨리 움직임!)
            self._speed = 3.5 # 속도 대폭 증가
            tail_rot = make_osc(3, frame % 3, 40, -25)
            if self.is_moving:
                legs_y = make_osc(2, frame % 2, 125, 120)
                body_y = make_osc(4, frame % 4, 110, 107)
                head_y = make_osc(4, frame % 4, 95, 93)
            tongue_scl = make_osc(3, frame % 3, 1.0, 1.3)
            show_tongue = True
            eye_type = "happy"  # ^^ 웃는 눈

        elif self._state == "alert":
            head_y = 90 if frame < 8 else 95
            ear_rot = 30 if frame < 8 else 10
            if frame < 7: alert_scl = 1.3
            elif frame < 11: alert_scl = 0.9
            elif frame < 15: alert_scl = 1.1
            elif frame < 35: alert_scl = 1.0
            eye_type = "dot" # 놀라서 똥그래진 점눈

        elif self._state == "celebrate":
            # 신나서 위아래로 퐁퐁 뛰기
            jy = make_osc(12, frame % 12, 0, -25)
            body_y, head_y, legs_y = 110 + jy, 95 + jy, 125 + jy
            shadow_scale = make_osc(12, frame % 12, 1.0, 0.6)
            tail_rot = make_osc(4, frame % 4, 40, -40)
            ear_rot = make_osc(12, frame % 12, -10, 30)
            show_tongue = True
            eye_type = "happy"

        elif self._state == "sleeping":
            # 바닥에 식빵 굽듯이 완벽히 찰싹 엎드림
            body_y = make_osc(tot, frame, 137, 140)
            head_y = make_osc(tot, frame, 125, 128)
            legs_y = 138
            shadow_scale = make_osc(tot, frame, 1.1, 1.0)
            tail_rot, eye_type = 20, "close"

        def _ellipse(cx, cy, rx, ry, col):
            p.setBrush(col)
            p.drawEllipse(QRectF(cx - rx, cy - ry, rx * 2, ry * 2))
        def _rect(cx, cy, w, h, r, col):
            p.setBrush(col)
            p.drawRoundedRect(QRectF(cx - w/2, cy - h/2, w, h), r, r)

        def _triangle_ear(scale_x, scale_y, col):
            path = QPainterPath()
            path.moveTo(0, -scale_y)       # 꼭대기 뾰족
            path.lineTo(-scale_x, scale_y) # 밑변 왼쪽
            path.lineTo(scale_x, scale_y)  # 밑변 오른쪽
            path.closeSubpath()
            p.setBrush(col)
            p.drawPath(path)

        # 1. 그림자 (더 길어진 몸통에 맞춰 그림자 폭, 위치 조정)
        p.save(); p.translate(100, 155); p.scale(shadow_scale, shadow_scale)
        _ellipse(0, 0, 60, 12, c_shadow); p.restore()

        swing_l, swing_r = 0.0, 0.0
        if self._state in ("idle", "working"):
            if self.is_moving:
                spd = 0.25 if self._state == "working" else 0.5 # 분모를 더 작게 줄여 다리를 훨씬 빨리 젓게 함 (기존 0.8 -> 0.25)
                swing_val = make_osc(tot/spd, frame % (tot/spd), -40, 40) # 다리 가동범위 축소화 (SD스럽게)
                swing_l, swing_r = swing_val, -swing_val
        elif self._state == "sleeping":
            swing_l, swing_r = 95, 75

        # 뒤쪽 뒷다리 (BG hind leg) - 짧고 앙증맞게
        p.save(); p.translate(45, legs_y - 8); p.rotate(swing_r)
        _rect(0, 10, 14, 18, 7, c_body.darker(110)); _ellipse(0, 18, 8, 6, c_belly.darker(110))
        p.restore()
        
        # 뒤쪽 앞다리 (BG fore leg)
        p.save(); p.translate(95, legs_y - 8); p.rotate(swing_l)
        _rect(0, 10, 14, 18, 7, c_body.darker(110)); _ellipse(0, 18, 8, 6, c_belly.darker(110))
        p.restore()
        
        # 꼬리 (짧고 하얀 솜사탕 엉덩이 꼬리)
        p.save(); p.translate(25, body_y - 12); p.rotate(30 + tail_rot)
        _ellipse(0, 0, 14, 14, c_belly) 
        p.restore()

        # 통통한 포테이토 모양의 몸통 (코기 특유의 커브를 타원 여러개로 구현)
        p.save(); p.translate(75, body_y)
        _ellipse(0, -2, 40, 22, c_body)    # 허리/등
        _ellipse(-30, 2, 22, 18, c_body)   # 엉덩이 볼륨
        _ellipse(30, -5, 26, 24, c_body)   # 가슴 볼륨

        # 아래쪽 하얀 배털 코팅
        _ellipse(0, 15, 38, 12, c_belly)
        _ellipse(-25, 12, 18, 10, c_belly)
        _ellipse(30, 12, 22, 14, c_belly)
        p.restore()

        # 앞쪽 뒷다리 (FG hind leg) - 허벅지 빵빵하게 추가
        p.save(); p.translate(55, legs_y - 4); p.rotate(swing_l)
        _ellipse(-3, -6, 18, 16, c_body) # 두꺼운 허벅지 볼륨
        _rect(0, 8, 14, 18, 7, c_body)
        _ellipse(0, 16, 8, 6, c_belly) # 하얀 양말
        p.restore()

        # 앞쪽 앞다리 (FG fore leg)
        p.save(); p.translate(100, legs_y - 4); p.rotate(swing_r)
        _rect(0, 8, 14, 18, 7, c_body)
        _ellipse(0, 16, 8, 6, c_belly) # 양말
        p.restore()

        # 앞가슴 하얀 털 (포근한 목덜미 털)
        p.save(); p.translate(115, body_y - 6)
        _ellipse(0, 0, 18, 22, c_belly)
        p.restore()

        # --- 큰 머리 헤드 배치 (정면 응시) ---
        p.save(); p.translate(125, head_y)
        
        # 쫑긋하고 크고 둥근귀 (왼쪽/오른쪽 뒤쪽 레이어로 먼저 렌더링)
        p.save(); p.translate(-16, -24); p.rotate(-15 + ear_rot)
        _triangle_ear(14, 25, c_ear)
        p.translate(0, 4); _triangle_ear(6, 17, c_ear_in)
        p.restore()

        p.save(); p.translate(16, -24); p.rotate(15 + ear_rot)
        _triangle_ear(14, 25, c_ear)
        p.translate(0, 4); _triangle_ear(6, 17, c_ear_in)
        p.restore()

        # 얼굴 빵빵 베이스 (가로로 더 넙적하게)
        _ellipse(0, 0, 42, 34, c_body)
        
        # 하얀 하트/볼살 얼굴 무늬 패턴 (참고 이미지 완벽 반영)
        p.save(); p.translate(-18, 10); p.rotate(25); _ellipse(0, 0, 22, 18, c_belly); p.restore()
        p.save(); p.translate(18, 10); p.rotate(-25); _ellipse(0, 0, 22, 18, c_belly); p.restore()
        _ellipse(0, 16, 22, 18, c_belly) # 주둥이 중심
        
        _ellipse(-14, -16, 5, 8, c_belly) # 왼쪽 눈썹 무늬
        _ellipse(14, -16, 5, 8, c_belly)  # 오른쪽 눈썹 무늬

        # 핑크빛 큰 볼터치
        _ellipse(-25, 10, 8, 4, c_cheek)
        _ellipse(25, 10, 8, 4, c_cheek)

        # 동그랗고 앙증맞은 까만 코
        _ellipse(0, 12, 7, 5, c_nose)
        _ellipse(-1, 11, 2, 1.5, QColor(255, 255, 255)) # 코 광택
        
        # 고양이 입 (w 모양)
        p.setPen(Qt.PenStyle.SolidLine)
        pen = QPainter.pen(p)
        pen.setColor(c_nose); pen.setWidth(2); pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        if self._state == "sleeping":
            path.moveTo(-6, 20); path.quadTo(-3, 23, 0, 20); path.quadTo(3, 23, 6, 20)
            p.drawPath(path)
        else:
            path.moveTo(-6, 18); path.quadTo(-3, 22, 0, 18); path.quadTo(3, 22, 6, 18)
            p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        
        # 붉은 스카프 초커 목걸이
        p.save(); p.translate(0, 29)
        _rect(0, 0, 34, 10, 5, QColor(230, 60, 60))
        # 가운데 매달린 금색 뼈다귀 모양 방울
        _rect(0, 5, 12, 5, 2, QColor(255, 215, 50))
        _ellipse(-6, 3, 4, 4, QColor(255, 215, 50)); _ellipse(-6, 7, 4, 4, QColor(255, 215, 50))
        _ellipse(6, 3, 4, 4, QColor(255, 215, 50)); _ellipse(6, 7, 4, 4, QColor(255, 215, 50))
        p.restore()

        # 크고 올망졸망한 눈 그리기 (눈동자 비율)
        eye_y = -1
        eye_gap = 16
        if eye_type == "open":
            _ellipse(-eye_gap, eye_y, 4.5, 6, c_nose); _ellipse(eye_gap, eye_y, 4.5, 6, c_nose)
            _ellipse(-eye_gap+1, eye_y-2, 2, 3, QColor(255, 255, 255)) # 초롱초롱 하이라이트
            _ellipse(eye_gap+1, eye_y-2, 2, 3, QColor(255, 255, 255))
        elif eye_type == "happy":
            # 신났을 때 둥글게 감은 눈 (^^)
            p.setPen(pen)
            h_path = QPainterPath()
            h_path.moveTo(-eye_gap-6, eye_y+2); h_path.quadTo(-eye_gap, eye_y-6, -eye_gap+6, eye_y+2)
            h_path.moveTo(eye_gap-6, eye_y+2); h_path.quadTo(eye_gap, eye_y-6, eye_gap+6, eye_y+2)
            p.drawPath(h_path)
            p.setPen(Qt.PenStyle.NoPen)
        elif eye_type == "dot":
            _ellipse(-eye_gap, eye_y, 4, 4, c_nose); _ellipse(eye_gap, eye_y, 4, 4, c_nose)
        elif eye_type == "close":
            _rect(-eye_gap, eye_y+3, 10, 3, 1.5, c_nose); _rect(eye_gap, eye_y+3, 10, 3, 1.5, c_nose)

        if show_tongue:
            p.save(); p.translate(0, 24); p.scale(1.0, tongue_scl)
            _ellipse(0, 6, 7, 10, c_tongue)
            p.setPen(pen)
            p.drawLine(0, 0, 0, 10)
            p.setPen(Qt.PenStyle.NoPen)
            p.restore()
        p.restore()
        
        if alert_scl > 0:
            p.save(); p.translate(160, 20); p.scale(alert_scl, alert_scl)
            _ellipse(0, 0, 18, 18, c_alert)
            _rect(0, -3, 5, 12, 2, QColor(255, 255, 255))
            _ellipse(0, 7, 2.5, 2.5, QColor(255, 255, 255))
            p.restore()

        if self._state == "celebrate":
            def draw_star(cx, cy, dly, clr):
                ap = min(dly + 6, tot - 1)
                fd = min(dly + 22, tot)
                if frame < dly or frame > fd: return
                opac = 1.0; scl = 1.0
                if frame <= ap: 
                    opac = (frame - dly) / (ap - dly) if ap > dly else 1.0
                    scl = 1.2 - 0.2*opac
                elif frame >= fd - 4:
                    opac = (fd - frame) / 4.0
                opac = max(0, min(1, opac))
                p.save(); p.translate(cx, cy); p.scale(scl, scl)
                c = QColor(clr); c.setAlphaF(opac); p.setBrush(c)
                path = QPainterPath()
                for j in range(10):
                    a = math.pi * j / 5 - math.pi / 2
                    r = 12 if j % 2 == 0 else 5
                    pt = QPointF(r * math.cos(a), r * math.sin(a))
                    if j == 0: path.moveTo(pt)
                    else: path.lineTo(pt)
                path.closeSubpath(); p.drawPath(path); p.restore()
            draw_star(55, 55, 0, QColor(255, 215, 50))
            draw_star(142, 42, 8, QColor(255, 215, 50))
            draw_star(155, 85, 16, QColor(255, 128, 204))
            draw_star(38, 82, 22, QColor(255, 215, 50))
            draw_star(100, 15, 4, QColor(255, 128, 204))

# ---------------------------------------------------------------------------
# 말풍선 위젯 & 스크립트 실행
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
        self._label.setMaximumWidth(400)
        self._label.setStyleSheet("color: #e6e6e6; font-size: 13px; padding: 4px;")
        self._label.setFont(QFont("Malgun Gothic", 11))
        
        self._label.setTextFormat(Qt.TextFormat.RichText)
        self._label.setOpenExternalLinks(True)
        self._label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_text(self, text: str, duration_ms: int = BUBBLE_MS):
        self._label.setText(text)
        self._label.adjustSize()
        w = max(50, self._label.width() + 28)
        h = max(20, self._label.height() + 28)
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

class ScriptWorker(QObject):
    finished = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        self._running = False

    def run(self, tag: str, cmd: list[str]):
        if self._running: return
        self._running = True
        def _work():
            try:
                r = subprocess.run(cmd, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace",
                                   cwd=str(_PROJECT_ROOT), timeout=20)
                res = r.stdout.strip()
            except Exception:
                res = ""
            finally:
                self._running = False
            self.finished.emit(tag, res)
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
        
        self._worker = ScriptWorker()
        self._worker.finished.connect(self._on_script_done)

        # LottiePlayer 대신 최적화된 NativeDogPlayer 세팅!
        self._players: dict[str, NativeDogPlayer] = {}
        for state, cfg in STATE_CONFIG.items():
            p = NativeDogPlayer(state, render_size=cfg["size"])
            p.set_speed(cfg["speed"])
            self._players[state] = p

        self._current_player: Optional[NativeDogPlayer] = None

        self._setup_window()
        self._setup_tray()
        self._setup_timers()
        self._set_state("sleeping")

    def _setup_window(self):
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("DayTracker 강아지 (Native)")
        screen = QApplication.primaryScreen().availableGeometry()
        size = STATE_CONFIG["sleeping"]["size"]
        self.resize(size + 20, size + 20)
        self.move(screen.right() - self.width() - 20, screen.bottom() - self.height() - 20)
        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.resize(self.width(), self.height())
        self.show()

    def _setup_tray(self):
        pix = QPixmap(16, 16); pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix); p.setFont(QFont("Segoe UI Emoji", 10)); p.drawText(0, 12, "🐶"); p.end()
        self._tray = QSystemTrayIcon(QIcon(pix), self); self._tray.setToolTip("🐶 DayTracker 강아지")
        menu = QMenu()
        menu.addAction(QAction("오늘 상태", self, triggered=self._on_click_status))
        menu.addAction(QAction("아침 브리핑", self, triggered=self._on_click_briefing))
        menu.addSeparator()
        
        test_menu = menu.addMenu("🧪 애니메이션 테스트 (상태 고정)")
        test_menu.addAction(QAction("Idle (대기/휴식)", self, triggered=lambda: self._force_state("idle")))
        test_menu.addAction(QAction("Working (작업중)", self, triggered=lambda: self._force_state("working")))
        test_menu.addAction(QAction("Alert (알람)", self, triggered=lambda: self._force_state("alert")))
        test_menu.addAction(QAction("Celebrate (축하)", self, triggered=lambda: self._force_state("celebrate")))
        test_menu.addAction(QAction("Sleeping (수면)", self, triggered=lambda: self._force_state("sleeping")))
        test_menu.addSeparator()
        test_menu.addAction(QAction("자동 전환으로 복귀", self, triggered=self._resume_auto_state))

        menu.addSeparator()
        menu.addAction(QAction("종료", self, triggered=QApplication.quit))
        self._tray.setContextMenu(menu); self._tray.show()

    def _force_state(self, state: str):
        self._tick_timer.stop()  # 자동 변경 정지
        self._set_state(state)
        self._show_bubble(f"수동 테스트: {state.upper()}", 3000)

    def _resume_auto_state(self):
        self._tick_timer.start(TICK_INTERVAL_MS)
        self._tick()  # 즉시 원래 상태(자동)로 갱신!
        self._show_bubble("자동 상태 전환 모드로 복귀했습니다.", 3000)

    def _setup_timers(self):
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._next_frame)
        self._anim_timer.start(33) # ~30 fps 부드러운 렌더

        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start(TICK_INTERVAL_MS)

        self._stuck_timer = QTimer(self)
        self._stuck_timer.timeout.connect(self._check_stuck)
        self._stuck_timer.start(STUCK_CHECK_MS)

        self._target_pos = None
        self._roam_timer = QTimer(self)
        self._roam_timer.timeout.connect(self._update_roam_target)
        self._roam_timer.start(4000)

    def _update_roam_target(self):
        if self._state in ("idle", "working"):
            if random.random() < 0.4:
                self._target_pos = None # 가끔 멈춰서 휴식
                return
            screen = QApplication.primaryScreen().availableGeometry()
            margin_right = screen.right() - self.width() - 20
            margin_bot = screen.bottom() - self.height() - 20
            if margin_right > screen.left() + 20 and margin_bot > screen.top() + 50:
                tx = random.randint(screen.left() + 50, margin_right)
                # 하단 근처에서만 왔다갔다
                ty = random.randint(max(screen.top() + 50, screen.bottom() - 250), margin_bot)
                self._target_pos = QPointF(float(tx), float(ty))
        else:
            self._target_pos = None

    def _set_state(self, state: str):
        if state not in STATE_CONFIG: state = "sleeping"
        self._state = state
        cfg = STATE_CONFIG[state]
        self._current_player = self._players.get(state)
        
        if self._current_player:
            self._current_player.set_speed(cfg["speed"])
            self._current_player.set_size(cfg["size"])
            self._current_player._frame_idx = 0.0

        new_size = cfg["size"] + 20
        old_center = self.geometry().center()
        
        self.resize(new_size, new_size); self._label.resize(new_size, new_size)
        
        # 상태가 변해 크기가 바뀌더라도, 사용자가 이동해둔 중심점 유지
        new_rect = self.geometry()
        new_rect.moveCenter(old_center)
        self.move(new_rect.topLeft())
        
        self.setWindowOpacity(cfg["opacity"])
        self._reposition_bubble()

    def _next_frame(self):
        if self._current_player:
            self._label.setPixmap(self._current_player.next_frame())
            
        # 화면 이곳저곳 돌아다니는 로직
        if getattr(self, "_drag_pos", None) is None and getattr(self, "_target_pos", None) and self._state in ("idle", "working"):
            curr = self.geometry().topLeft()
            dx = self._target_pos.x() - curr.x()
            dy = self._target_pos.y() - curr.y()
            dist = math.hypot(dx, dy)
            if dist > 3:
                if self._current_player:
                    self._current_player.is_moving = True
                    self._current_player.is_flipped = (dx < 0) # 왼쪽 이동 시 좌우 반전
                speed = 5.0 if self._state == "working" else 1.5
                vx = (dx / dist) * speed
                vy = (dy / dist) * speed
                new_x = curr.x() + vx
                new_y = curr.y() + vy
                self.move(int(new_x), int(new_y))
                if self._bubble.isVisible():
                    self._reposition_bubble()
            else:
                if self._current_player: self._current_player.is_moving = False
                self._target_pos = None
        else:
            if self._current_player: self._current_player.is_moving = False

    def _reposition_bubble(self):
        gpos = self.mapToGlobal(QPoint(0, 0))
        # Use actual exact current sizes evaluated accurately instead of cached dimensions
        bw = self._bubble.width()
        bh = self._bubble.height()
        
        # Position slightly left entirely and way above character
        tx = gpos.x() - bw + 20
        ty = gpos.y() - bh + 20
        
        screen = QApplication.primaryScreen().availableGeometry()
        if tx < screen.left(): tx = screen.left() + 20
        if ty < screen.top(): ty = screen.top() + 20
        
        self._bubble.setGeometry(tx, ty, bw, bh)

    def _show_bubble(self, text: str, duration_ms: int = BUBBLE_MS):
        # Must show/resize the bubble FIRST so it gains the new geometry length
        self._bubble.show_text(text, duration_ms)
        # Then reposition its top-left coordinates taking into account the new bounding box
        self._reposition_bubble()
    def _hide_bubble(self): self._bubble.hide()

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
                if moved < 6: self._on_click_status()
            self._drag_pos = None
        elif e.button() == Qt.MouseButton.RightButton:
            self._on_click_briefing()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            text, ok = QInputDialog.getText(
                None, "강아지에게 밥 주기 (일정/대화)", 
                "🐶 일정을 입력해주세요! (예: 14:00 미팅, 오후 2시 30분 미팅)", 
                QLineEdit.EchoMode.Normal, ""
            )
            if ok and text.strip():
                self._handle_user_input(text.strip())

    def _handle_user_input(self, text: str):
        # 정규식 패턴 수정: '14:00', '14시', '2시 30분', '14시30분' 등
        time_match = re.search(r'([0-1]?[0-9]|2[0-3])\s*[:시]\s*([0-5][0-9])?[분]?', text)
        if time_match:
            filepath = DATA_DIR / "schedules.json"
            schedules = []
            if filepath.exists():
                try: 
                    with open(filepath, "r", encoding="utf-8") as f:
                        schedules = json.load(f)
                except Exception: pass
            
            schedules.append({
                "timestamp": datetime.now().isoformat(),
                "text": text,
                "notified": False
            })
            
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(schedules, f, ensure_ascii=False, indent=2)
            
            self._set_state("celebrate")
            self._show_bubble(f"🐶 [간식 냠냠!] 일정을 기억할게요!<br>👉 <b>{text}</b>", 6000)
        else:
            self._set_state("idle")
            self._show_bubble(f"🐶 왈왈! '{text}' 라고 하셨군요!<br>(저는 시간에 관련된 일정을 제일 잘 외워요!)", 6000)

    def _on_click_status(self):
        if self._bubble.isVisible():
            self._hide_bubble()
            return
        text = self._query_today_status()
        html_text = text.replace("\n", "<br>")
        self._show_bubble(html_text, 8000)
        self._set_state("alert")
        QTimer.singleShot(3000, lambda: self._set_state("idle"))

    def _on_click_briefing(self):
        script = _PROJECT_ROOT / "scripts" / "agents" / "morning_briefing.py"
        if script.exists():
            self._set_state("alert")
            self._show_bubble("브리핑 준비중...", 2000)
            self._worker.run("briefing", [sys.executable, str(script)]) # Removed --dry-run
        else:
            self._show_bubble("morning_briefing.py 스크립트가 없네요.", 4000)

    def _on_script_done(self, tag: str, text: str):
        if tag == "briefing":
            import urllib.parse
            note_path = None
            lines = text.strip().split("\n")
            
            # 파이썬 출력 마지막 줄에서 노트 저장 경로를 추출
            if lines and "Briefing written to:" in lines[-1]:
                note_path = lines[-1].split("Briefing written to:", 1)[1].strip()
                lines = lines[:-1]
            
            clean_text = "\n".join(lines).strip().replace("\n", "<br>")
            if note_path:
                # 절대 경로는 윈도우/옵시디언 설정에 따라 파싱 에러(Vault Not Found)가 날 수 있으므로,
                # 옵시디언이 열려있는 현재 Vault 내에서 파일 이름으로 바로 검색해서 열게 합니다.
                file_name = Path(note_path).name
                obsidian_url = f"obsidian://open?file={urllib.parse.quote(file_name)}"
                clean_text += f'<br><br><a href="{obsidian_url}" style="color: #66b3ff; text-decoration: none; font-weight: bold;">🔗 옵시디언 노트 열기</a>'

            if clean_text: self._show_bubble(clean_text, 15_000)  # 확인/클릭할 시간을 위해 15초 표시
            else: self._show_bubble("🐶 브리핑 거리가 없어요!", 4000)
            QTimer.singleShot(3000, lambda: self._set_state("sleeping"))

        elif tag == "stuck":
            if text.strip():
                self._set_state("alert")
                html_text = f"🐶 혹시 막히셨나요?<br>{text}".replace("\n", "<br>")
                self._show_bubble(html_text, 8000)
                QTimer.singleShot(9000, lambda: self._set_state("idle"))

    def _tick(self):
        minutes = self._minutes_since_last_activity()
        if minutes is not None:
            if minutes > 30: self._set_state("sleeping")
            elif minutes < 5: self._set_state("working")
            else:
                if self._state not in ("alert", "celebrate"):
                    self._set_state("idle")
        self._check_schedules()

    def _check_schedules(self):
        filepath = DATA_DIR / "schedules.json"
        if not filepath.exists(): return
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                schedules = json.load(f)
        except Exception: return
        
        now = datetime.now()
        changed = False
        
        for s in schedules:
            if s.get("notified"): continue
            text = s.get("text", "")
            m = re.search(r'([0-1]?[0-9]|2[0-3])\s*[:시]\s*([0-5][0-9])?[분]?', text)
            if m:
                h = int(m.group(1))
                if "오후" in text and h < 12: h += 12 # 간단한 오후 보정
                # 2시 인데 오후/오전 명시 없을경우, 현재시간보다 전이면 오후로 치환
                m_str = m.group(2)
                mn = int(m_str) if m_str else 0
                
                try:
                    dt = now.replace(hour=h, minute=mn, second=0, microsecond=0)
                    if h < 12 and "오후" not in text and dt < now:
                        dt = now.replace(hour=h+12, minute=mn, second=0, microsecond=0)
                except ValueError:
                    continue
                
                diff_mins = (dt - now).total_seconds() / 60.0
                
                # 11분 전 ~ 0분 전 사이에 진입하면 푸시 알림
                if 0 <= diff_mins <= 11:
                    self._set_state("alert")
                    self._show_bubble(f"🐶 [일정 알림]<br>곧 일정이 시작돼요!<br>👉 <b>{text}</b>", 15000)
                    s["notified"] = True
                    changed = True
                elif diff_mins < 0:
                    s["notified"] = True # 지난 일정 무시
                    changed = True
                    
        if changed:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(schedules, f, ensure_ascii=False, indent=2)

    def _check_stuck(self):
        script = _PROJECT_ROOT / "scripts" / "agents" / "stuck_detector.py"
        if script.exists():
            self._worker.run("stuck", [sys.executable, str(script), "--short", "--threshold-minutes", "30"])

    def _minutes_since_last_activity(self) -> Optional[int]:
        db = DATA_DIR / "worklog.db"
        if not db.exists(): return None
        try:
            conn = sqlite3.connect(str(db))
            row = conn.execute("SELECT MAX(timestamp) FROM file_events").fetchone()
            conn.close()
            if not row or not row[0]: return None
            last = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
            now = datetime.now(tz=timezone.utc)
            return int((now - last).total_seconds() / 60)
        except Exception:
            return None

    def _query_today_status(self) -> str:
        db = DATA_DIR / "worklog.db"
        if not db.exists(): return "🐶 안녕하세요!\nDB가 아직 작동 전인가 봐요."
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            conn = sqlite3.connect(str(db))
            ai_count = conn.execute("SELECT COUNT(*) FROM ai_prompts WHERE timestamp LIKE ?", (f"{today}%",)).fetchone()[0]
            file_count = conn.execute("SELECT COUNT(*) FROM file_events WHERE timestamp LIKE ?", (f"{today}%",)).fetchone()[0]
            projects = conn.execute("""
                SELECT COALESCE(p.name, fe.file_path), COUNT(*) as c
                FROM file_events fe LEFT JOIN projects p ON fe.project_id = p.id
                WHERE fe.timestamp LIKE ? GROUP BY 1 ORDER BY c DESC LIMIT 3
            """, (f"{today}%",)).fetchall()
            conn.close()
            proj_str = " | ".join(f"{n}({c})" for n, c in projects) if projects else "없음"
            return f"🐶 오늘 현황\nAI 조수 호출: {ai_count}건 | 파일 수정: {file_count}건\n주요 작업: {proj_str}"
        except Exception as ex:
            return f"조회 중 오류가 났네요! {ex}"

def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    # Lottie 검증 로직 자체를 삭제했으므로 파일이 없어도 완벽 실행.
    dog = DogCharacter()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
