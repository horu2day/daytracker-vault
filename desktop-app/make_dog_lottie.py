"""
desktop-app/make_dog_lottie.py
-------------------------------
Python으로 상태별 강아지 Lottie JSON을 생성한다.
rlottie-python은 'el' (ellipse) shape를 렌더링하지 못하므로
베지어 패스('sh' type)로 모든 도형을 직접 그린다.

생성 파일:
  data/puppy_idle.json      - 살랑살랑 꼬리 + 눈 깜빡
  data/puppy_working.json   - 빠른 꼬리 + 달리기 + 혀
  data/puppy_alert.json     - 귀 쫑긋 + 느낌표 반짝
  data/puppy_celebrate.json - 점프 + 별 파티클

Usage:
    python desktop-app/make_dog_lottie.py
    python desktop-app/make_dog_lottie.py --out-dir data/
"""

from __future__ import annotations
import argparse
import json
import math
from pathlib import Path

# ---------------------------------------------------------------------------
# 색상 팔레트 (RGBA 0-1)
# ---------------------------------------------------------------------------
BODY_COLOR    = [0.87, 0.72, 0.53, 1]
DARK_BROWN    = [0.42, 0.28, 0.14, 1]
EAR_COLOR     = [0.75, 0.55, 0.35, 1]
NOSE_COLOR    = [0.20, 0.10, 0.10, 1]
EYE_COLOR     = [0.15, 0.10, 0.08, 1]
WHITE         = [1.00, 1.00, 1.00, 1]
TAIL_COLOR    = [0.78, 0.60, 0.40, 1]
TONGUE_COLOR  = [0.95, 0.40, 0.50, 1]
STAR_YELLOW   = [1.00, 0.85, 0.20, 1]
STAR_PINK     = [1.00, 0.50, 0.80, 1]
ALERT_RED     = [0.95, 0.25, 0.20, 1]

# 배경 원 색상 — 어두운 바탕에서도 캐릭터가 잘 보이도록
# (상태별로 색상 구분)
BG_IDLE       = [0.98, 0.96, 0.88, 0.92]   # 크림색, 92% 불투명
BG_WORKING    = [0.88, 0.98, 0.88, 0.92]   # 연두색
BG_ALERT      = [1.00, 0.92, 0.82, 0.92]   # 연주황
BG_CELEBRATE  = [0.95, 0.88, 1.00, 0.92]   # 연보라
BG_SLEEPING   = [0.85, 0.90, 0.98, 0.92]   # 연파랑


# ---------------------------------------------------------------------------
# 베지어 패스 헬퍼
# ---------------------------------------------------------------------------

KAPPA = 0.5519150244935105   # 원을 4개 큐빅 세그먼트로 근사하는 상수

def oval_path(cx: float, cy: float, rx: float, ry: float) -> dict:
    """타원 베지어 경로."""
    kx, ky = KAPPA * rx, KAPPA * ry
    return {
        "v": [
            [cx,      cy - ry],
            [cx + rx, cy     ],
            [cx,      cy + ry],
            [cx - rx, cy     ],
        ],
        "i": [[-kx, 0], [0, -ky], [kx, 0], [0, ky]],
        "o": [[kx, 0], [0, ky], [-kx, 0], [0, -ky]],
        "c": True,
    }


def rect_path(cx: float, cy: float, w: float, h: float, r: float = 0) -> dict:
    """둥근 사각형 베지어 경로 (r=모서리 반지름)."""
    r = min(r, w / 2, h / 2)
    x0, x1 = cx - w / 2, cx + w / 2
    y0, y1 = cy - h / 2, cy + h / 2
    k = KAPPA * r
    if r == 0:
        return {
            "v": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
            "i": [[0, 0], [0, 0], [0, 0], [0, 0]],
            "o": [[0, 0], [0, 0], [0, 0], [0, 0]],
            "c": True,
        }
    return {
        "v": [
            [x0 + r, y0], [x1 - r, y0],
            [x1, y0 + r], [x1, y1 - r],
            [x1 - r, y1], [x0 + r, y1],
            [x0, y1 - r], [x0, y0 + r],
        ],
        "i": [
            [0, 0],  [-k, 0],
            [0, -k], [0, 0],
            [0, 0],  [k, 0],
            [0, k],  [0, 0],
        ],
        "o": [
            [k, 0],  [0, 0],
            [0, 0],  [0, k],
            [-k, 0], [0, 0],
            [0, 0],  [0, -k],
        ],
        "c": True,
    }


def star_path(cx: float, cy: float, r_outer: float, r_inner: float, n: int = 5) -> dict:
    """별 모양 경로."""
    verts, ins, outs = [], [], []
    for i in range(n * 2):
        angle = math.pi * i / n - math.pi / 2
        r = r_outer if i % 2 == 0 else r_inner
        verts.append([cx + r * math.cos(angle), cy + r * math.sin(angle)])
        ins.append([0, 0])
        outs.append([0, 0])
    return {"v": verts, "i": ins, "o": outs, "c": True}


# ---------------------------------------------------------------------------
# Lottie 빌더 헬퍼
# ---------------------------------------------------------------------------

def static(value) -> dict:
    return {"a": 0, "k": value}


def animated(keyframes: list) -> dict:
    return {"a": 1, "k": keyframes}


def kf(t: int, s, e=None, ei: float = 0.5, eo: float = 0.5) -> dict:
    """키프레임 하나. e=None이면 홀드."""
    frame = {
        "t": t,
        "s": s if isinstance(s, list) else [s],
        "i": {"x": [ei], "y": [1]},
        "o": {"x": [eo], "y": [0]},
    }
    if e is not None:
        frame["e"] = e if isinstance(e, list) else [e]
    return frame


def shape_group(nm: str, items: list) -> dict:
    """shapes 배열 안에 들어가는 gr(group)."""
    return {
        "ty": "gr",
        "nm": nm,
        "it": items + [{
            "ty": "tr",
            "p": static([0, 0]),
            "a": static([0, 0]),
            "s": static([100, 100]),
            "r": static(0),
            "o": static(100),
            "sk": static(0),
            "sa": static(0),
        }],
    }


def path_shape(path_data: dict) -> dict:
    return {"ty": "sh", "nm": "path", "ks": static(path_data)}


def fill_shape(color: list, opacity: float = 100) -> dict:
    return {"ty": "fl", "nm": "fill", "c": static(color), "o": static(opacity), "r": 1}


def stroke_shape(color: list, width: float = 2.0) -> dict:
    return {"ty": "st", "nm": "stroke", "c": static(color), "w": static(width),
            "o": static(100), "lc": 2, "lj": 2}


def solid_oval(nm: str, cx: float, cy: float, rx: float, ry: float,
               fill: list, stroke: list = None, sw: float = 2.0) -> dict:
    items = [path_shape(oval_path(cx, cy, rx, ry)), fill_shape(fill)]
    if stroke:
        items.append(stroke_shape(stroke, sw))
    return shape_group(nm, items)


def layer_base(ind: int, nm: str, op: int,
               px=100, py=100,
               pos_kf=None, rot_kf=None, scale_kf=None,
               opacity_kf=None, opacity: float = 100) -> dict:
    pos_prop  = animated(pos_kf)  if pos_kf  else static([px, py, 0])
    rot_prop  = animated(rot_kf)  if rot_kf  else static(0)
    scl_prop  = animated(scale_kf) if scale_kf else static([100, 100, 100])
    opa_prop  = animated(opacity_kf) if opacity_kf else static(opacity)
    return {
        "ind": ind, "ty": 4, "nm": nm,
        "ip": 0, "op": op, "st": 0, "bm": 0,
        "ks": {
            "p": pos_prop,
            "a": static([0, 0, 0]),
            "s": scl_prop,
            "r": rot_prop,
            "o": opa_prop,
        },
        "shapes": [],
    }


def lottie_doc(nm: str, fr: int, op: int, layers: list) -> dict:
    return {
        "v": "5.5.7",
        "meta": {"g": "make_dog_lottie.py"},
        "fr": fr, "ip": 0, "op": op,
        "w": 200, "h": 200,
        "nm": nm, "ddd": 0,
        "assets": [], "layers": layers,
    }


# ---------------------------------------------------------------------------
# 강아지 파트별 shapes (좌표는 레이어 로컬 기준)
# ---------------------------------------------------------------------------

def body_shapes() -> list:
    return [solid_oval("body", 0, 0, 38, 26, BODY_COLOR, DARK_BROWN)]


def head_shapes() -> list:
    return [solid_oval("head", 0, 0, 30, 28, BODY_COLOR, DARK_BROWN)]


def ear_shapes() -> list:
    return [
        solid_oval("left_ear",  -20, -14, 12, 18, EAR_COLOR, DARK_BROWN),
        solid_oval("right_ear",  20, -14, 12, 18, EAR_COLOR, DARK_BROWN),
    ]


def face_shapes(eye_squint: bool = False) -> list:
    """눈 + 코. eye_squint=True이면 행복한 눈(scaleY 0.4)."""
    ey = 0.4 if eye_squint else 1.0
    shapes = [
        # 눈 흰자 (생략 — 어두운 눈으로 단순화)
        solid_oval("left_eye",   -13, -4, 6,  int(6*ey)+1, EYE_COLOR),
        solid_oval("right_eye",   13, -4, 6,  int(6*ey)+1, EYE_COLOR),
        # 하이라이트
        solid_oval("left_hi",   -10, -7, 2, 2, WHITE),
        solid_oval("right_hi",   16, -7, 2, 2, WHITE),
        # 코
        solid_oval("nose",        0,  6, 8,  5, NOSE_COLOR),
    ]
    return shapes


def tail_shapes() -> list:
    # 위로 굽은 꼬리: 긴 타원
    return [solid_oval("tail", 0, -18, 7, 20, TAIL_COLOR, DARK_BROWN)]


def leg_shapes() -> list:
    return [
        solid_oval("leg_fl", -23, 10, 6, 11, BODY_COLOR, DARK_BROWN, 1.5),
        solid_oval("leg_fr",  -8, 10, 6, 11, BODY_COLOR, DARK_BROWN, 1.5),
        solid_oval("leg_rl",   8, 10, 6, 11, BODY_COLOR, DARK_BROWN, 1.5),
        solid_oval("leg_rr",  23, 10, 6, 11, BODY_COLOR, DARK_BROWN, 1.5),
    ]


def tongue_shapes() -> list:
    return [
        solid_oval("tongue", 0, 14, 7, 9, TONGUE_COLOR),
    ]


def bg_circle_layer(ind: int, op: int, color: list) -> dict:
    """어두운 바탕에서도 캐릭터가 잘 보이도록 뒤에 깔리는 둥근 배경 레이어."""
    layer = layer_base(ind, "bg_circle", op, px=100, py=100)
    layer["shapes"] = [
        shape_group("bg", [
            path_shape(oval_path(0, 0, 90, 90)),
            fill_shape(color),
        ])
    ]
    return layer


# ---------------------------------------------------------------------------
# idle: 꼬리 흔들기 + 눈 깜빡 + 가볍게 보빙
# ---------------------------------------------------------------------------

def make_idle(op: int = 90) -> dict:
    # 꼬리 좌우
    tail_rot = [
        kf(0,  [30], [-12]),
        kf(15, [-12], [30]),
        kf(30, [30], [-12]),
        kf(45, [-12], [30]),
        kf(60, [30], [-12]),
        kf(75, [-12], [30]),
        {"t": op, "s": [30]},
    ]
    # 머리/몸 위아래 (22프레임 주기)
    head_pos = [
        kf(0,  [100, 68, 0], [100, 64, 0]),
        kf(22, [100, 64, 0], [100, 68, 0]),
        kf(45, [100, 68, 0], [100, 64, 0]),
        kf(67, [100, 64, 0], [100, 68, 0]),
        {"t": op, "s": [100, 68, 0]},
    ]
    body_pos = [
        kf(0,  [100, 112, 0], [100, 108, 0]),
        kf(22, [100, 108, 0], [100, 112, 0]),
        kf(45, [100, 112, 0], [100, 108, 0]),
        kf(67, [100, 108, 0], [100, 112, 0]),
        {"t": op, "s": [100, 112, 0]},
    ]

    # 눈 깜빡: 프레임 60-64에 scaleY 축소
    # 구현 방법: 눈을 별도 레이어로 분리, scaleY 애니메이션
    blink_scale = [
        kf(0,  [100, 100, 100], [100, 100, 100]),
        kf(60, [100, 100, 100], [100,   8, 100]),
        kf(62, [100,   8, 100], [100, 100, 100]),
        kf(64, [100, 100, 100], [100, 100, 100]),
        {"t": op, "s": [100, 100, 100]},
    ]

    # 레이어 빌드
    tail  = layer_base(1, "tail",  op, px=148, py=106, rot_kf=tail_rot)
    tail["shapes"] = tail_shapes()

    body  = layer_base(2, "body",  op, pos_kf=body_pos)
    body["shapes"] = body_shapes()

    legs  = layer_base(3, "legs",  op, px=100, py=128)
    legs["shapes"] = leg_shapes()

    ears  = layer_base(4, "ears",  op, px=100, py=68)
    ears["shapes"] = ear_shapes()

    head  = layer_base(5, "head",  op, pos_kf=head_pos)
    head["shapes"] = head_shapes()

    # 눈 레이어 분리 (scaleY 애니메이션)
    eyes  = layer_base(6, "eyes",  op, px=100, py=68, scale_kf=blink_scale)
    eyes["ks"]["a"] = static([0, -4, 0])   # 눈 위치 기준점
    eyes["shapes"] = [
        solid_oval("left_eye",  -13, -4, 6, 6, EYE_COLOR),
        solid_oval("right_eye",  13, -4, 6, 6, EYE_COLOR),
        solid_oval("left_hi",  -10, -7, 2, 2, WHITE),
        solid_oval("right_hi",  16, -7, 2, 2, WHITE),
        solid_oval("nose",        0,  6, 8, 5, NOSE_COLOR),
    ]

    bg = bg_circle_layer(99, op, BG_IDLE)
    return lottie_doc("puppy_idle", 30, op,
                      [tail, body, legs, ears, head, eyes, bg])


# ---------------------------------------------------------------------------
# working: 빠른 꼬리 + 달리는 다리 + 혀
# ---------------------------------------------------------------------------

def make_working(op: int = 60) -> dict:
    # 꼬리 빠르게 (6프레임 주기)
    tail_rot = []
    for i in range(op // 6 + 2):
        t = i * 6
        if t >= op:
            break
        angle = [40] if i % 2 == 0 else [-12]
        nxt   = [-12] if i % 2 == 0 else [40]
        tail_rot.append(kf(t, angle, nxt))
    tail_rot.append({"t": op, "s": [40]})

    # 다리 달리기 (4프레임 주기) - Y 위치 교대
    def make_leg_pos(offset: int):
        pos = []
        for i in range(op // 4 + 2):
            t = i * 4 + offset
            if t >= op:
                break
            y  = [100, 125, 0] if i % 2 == 0 else [100, 132, 0]
            yn = [100, 132, 0] if i % 2 == 0 else [100, 125, 0]
            pos.append(kf(t, y, yn))
        pos.append({"t": op, "s": [100, 125, 0]})
        return pos

    tail  = layer_base(1, "tail",     op, px=148, py=106, rot_kf=tail_rot)
    tail["shapes"] = tail_shapes()

    body  = layer_base(2, "body",     op, px=100, py=112)
    body["shapes"] = body_shapes()

    legs  = layer_base(3, "legs",     op, pos_kf=make_leg_pos(0))
    legs["shapes"] = leg_shapes()

    ears  = layer_base(4, "ears",     op, px=100, py=68)
    ears["shapes"] = ear_shapes()

    head  = layer_base(5, "head",     op, px=100, py=68)
    head["shapes"] = head_shapes()

    eyes  = layer_base(6, "eyes",     op, px=100, py=68)
    eyes["shapes"] = face_shapes(eye_squint=True)  # 반달 눈

    tongue = layer_base(7, "tongue",  op, px=100, py=76)
    tongue["shapes"] = tongue_shapes()

    bg = bg_circle_layer(99, op, BG_WORKING)
    return lottie_doc("puppy_working", 30, op,
                      [tongue, tail, body, legs, ears, head, eyes, bg])


# ---------------------------------------------------------------------------
# alert: 귀 쫑긋 + 느낌표 원 반짝
# ---------------------------------------------------------------------------

def make_alert(op: int = 45) -> dict:
    # 귀 스케일 Y 살짝 위로 (높이 늘리기로 쫑긋 표현)
    ear_scale = [
        kf(0, [100, 100, 100], [100, 130, 100]),
        kf(8, [100, 130, 100], [100, 130, 100]),
        {"t": op, "s": [100, 130, 100]},
    ]

    # 느낌표 원 (팝업)
    alert_scale = [
        kf(0,  [0,   0,   100], [130, 130, 100]),
        kf(7,  [130, 130, 100], [90,  90,  100]),
        kf(11, [90,  90,  100], [110, 110, 100]),
        kf(15, [110, 110, 100], [100, 100, 100]),
        kf(35, [100, 100, 100], [0,   0,   100]),
        {"t": op, "s": [0, 0, 100]},
    ]
    alert_ring = layer_base(8, "alert_ring", op, px=158, py=48,
                            scale_kf=alert_scale)
    alert_ring["ks"]["a"] = static([0, 0, 0])
    alert_ring["shapes"] = [
        shape_group("ring", [
            path_shape(oval_path(0, 0, 14, 14)),
            fill_shape(ALERT_RED),
        ]),
        shape_group("excl_bar", [
            path_shape(rect_path(0, -3, 4, 9, 2)),
            fill_shape(WHITE),
        ]),
        shape_group("excl_dot", [
            path_shape(oval_path(0, 6, 2, 2)),
            fill_shape(WHITE),
        ]),
    ]

    tail  = layer_base(1, "tail",  op, px=148, py=106)
    tail["shapes"] = tail_shapes()

    body  = layer_base(2, "body",  op, px=100, py=112)
    body["shapes"] = body_shapes()

    legs  = layer_base(3, "legs",  op, px=100, py=128)
    legs["shapes"] = leg_shapes()

    ears  = layer_base(4, "ears",  op, px=100, py=68, scale_kf=ear_scale)
    ears["ks"]["a"] = static([0, 8, 0])   # 귀 아래쪽 기준점으로 위로 자람
    ears["shapes"] = ear_shapes()

    head  = layer_base(5, "head",  op, px=100, py=68)
    head["shapes"] = head_shapes()

    eyes  = layer_base(6, "eyes",  op, px=100, py=68)
    eyes["shapes"] = face_shapes()

    bg = bg_circle_layer(99, op, BG_ALERT)
    return lottie_doc("puppy_alert", 30, op,
                      [alert_ring, tail, body, legs, ears, head, eyes, bg])


# ---------------------------------------------------------------------------
# celebrate: 점프 + 별 파티클
# ---------------------------------------------------------------------------

def make_celebrate(op: int = 75) -> dict:
    # 점프 (위아래 2회)
    def jump_pos(base_y: float):
        return [
            kf(0,  [100, base_y,      0], [100, base_y - 55, 0], eo=0.1),
            kf(18, [100, base_y - 55, 0], [100, base_y,      0], ei=0.1),
            kf(38, [100, base_y,      0], [100, base_y - 55, 0], eo=0.1),
            kf(56, [100, base_y - 55, 0], [100, base_y,      0], ei=0.1),
            {"t": op, "s": [100, base_y, 0]},
        ]

    # 꼬리 매우 빠르게
    tail_rot = []
    for i in range(op // 5 + 2):
        t = i * 5
        if t >= op:
            break
        tail_rot.append(kf(t, [40] if i%2==0 else [-15],
                             [-15] if i%2==0 else [40]))
    tail_rot.append({"t": op, "s": [40]})

    # 별 파티클 생성 함수
    def star_layer(ind: int, cx: float, cy: float, delay: int, color: list):
        appear = min(delay + 6, op - 1)
        fade   = min(delay + 22, op)
        opa_kf = [
            kf(max(0, delay-1), [0],   [100]),
            kf(appear,          [100], [100]),
            kf(fade - 4,        [100], [0]),
            {"t": fade, "s": [0]},
        ]
        scl_kf = [
            kf(max(0, delay-1), [0,  0,  100], [120, 120, 100]),
            kf(appear,          [120,120,100], [80,  80,  100]),
            kf(appear + 4,      [80, 80, 100], [100, 100, 100]),
            {"t": op, "s": [0, 0, 100]},
        ]
        lyr = layer_base(ind, f"star{ind}", op, px=cx, py=cy,
                         scale_kf=scl_kf, opacity_kf=opa_kf)
        lyr["shapes"] = [
            shape_group("star", [
                path_shape(star_path(0, 0, 9, 4, 5)),
                fill_shape(color),
            ])
        ]
        return lyr

    stars = [
        star_layer(10,  55,  45,  0,  STAR_YELLOW),
        star_layer(11, 152,  38,  8,  STAR_YELLOW),
        star_layer(12, 165,  85, 16,  STAR_PINK),
        star_layer(13,  38,  82, 22,  STAR_YELLOW),
        star_layer(14, 100,  20,  4,  STAR_PINK),
    ]

    tail   = layer_base(1, "tail",    op, px=148, py=106, rot_kf=tail_rot)
    tail["shapes"] = tail_shapes()

    body   = layer_base(2, "body",    op, pos_kf=jump_pos(112))
    body["shapes"] = body_shapes()

    legs   = layer_base(3, "legs",    op, pos_kf=jump_pos(128))
    legs["shapes"] = leg_shapes()

    ears   = layer_base(4, "ears",    op, pos_kf=jump_pos(68))
    ears["shapes"] = ear_shapes()

    head   = layer_base(5, "head",    op, pos_kf=jump_pos(68))
    head["shapes"] = head_shapes()

    eyes   = layer_base(6, "eyes",    op, pos_kf=jump_pos(68))
    eyes["shapes"] = face_shapes(eye_squint=True)

    tongue = layer_base(7, "tongue",  op, pos_kf=jump_pos(78))
    tongue["shapes"] = tongue_shapes()

    bg = bg_circle_layer(99, op, BG_CELEBRATE)
    return lottie_doc("puppy_celebrate", 30, op,
                      stars + [tongue, tail, body, legs, ears, head, eyes, bg])


# ---------------------------------------------------------------------------
# 엔트리포인트
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="상태별 강아지 Lottie JSON 생성")
    parser.add_argument("--out-dir", default="data", help="출력 폴더 (기본: data/)")
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    makers = {
        "puppy_idle.json":      make_idle,
        "puppy_working.json":   make_working,
        "puppy_alert.json":     make_alert,
        "puppy_celebrate.json": make_celebrate,
    }

    for fname, fn in makers.items():
        doc = fn()
        path = out / fname
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] {path}  ({doc['op']}프레임 @ {doc['fr']}fps)")

    print("\n완료! 실행: python desktop-app/launch.py --lottie")


if __name__ == "__main__":
    main()
