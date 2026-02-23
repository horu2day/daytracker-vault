"""
desktop-app/make_dog_lottie.py
-------------------------------
Python으로 상태별 고품질 강아지 Lottie JSON을 생성한다.
RLottie-python의 제약을 피하기 위해 베지어 패스로 모든 도형을 구성하며,
각 상태별로 움직임 보간, 다관절 부모-자식 레이어링, 풍부한 액션을 적용합니다.
"""
from __future__ import annotations
import argparse, json, math
from pathlib import Path

# --- 고품질 팔레트 ---
BODY_COLOR    = [0.85, 0.55, 0.25, 1]  # 따뜻한 브라운
BELLY_COLOR   = [1.00, 0.92, 0.80, 1]  # 밝은 톤 (주둥이, 가슴, 발)
EAR_COLOR     = [0.70, 0.40, 0.15, 1]  # 귀
SPOT_COLOR    = [0.70, 0.40, 0.15, 1]  # 눈 주변 얼룩
NOSE_COLOR    = [0.15, 0.10, 0.10, 1]  # 코
EYE_COLOR     = [0.15, 0.10, 0.10, 1]  # 눈
WHITE         = [1.00, 1.00, 1.00, 1]
TAIL_COLOR    = [0.85, 0.55, 0.25, 1]
TONGUE_COLOR  = [0.95, 0.40, 0.50, 1]  # 혀
COLLAR_COLOR  = [0.90, 0.20, 0.25, 1]  # 빨간 목줄
TAG_COLOR     = [1.00, 0.80, 0.10, 1]  # 이름표 (금색)

SHADOW_COLOR  = [0.0, 0.0, 0.0, 0.08]  # 그림자

ALERT_RED     = [0.95, 0.25, 0.20, 1]
STAR_YELLOW   = [1.00, 0.85, 0.20, 1]
STAR_PINK     = [1.00, 0.50, 0.80, 1]

# --- 상태별 배경 ---
BG_IDLE       = [0.98, 0.96, 0.88, 0.92]
BG_WORKING    = [0.88, 0.98, 0.88, 0.92]
BG_ALERT      = [1.00, 0.92, 0.82, 0.92]
BG_CELEBRATE  = [0.95, 0.88, 1.00, 0.92]
BG_SLEEPING   = [0.85, 0.90, 0.98, 0.92]

KAPPA = 0.5519150244935105

# ---------------------------------------------------------------------------
# 베지어 헬퍼
# ---------------------------------------------------------------------------
def oval_path(cx: float, cy: float, rx: float, ry: float) -> dict:
    kx, ky = KAPPA * rx, KAPPA * ry
    return {
        "v": [[cx, cy-ry], [cx+rx, cy], [cx, cy+ry], [cx-rx, cy]],
        "i": [[-kx, 0], [0, -ky], [kx, 0], [0, ky]],
        "o": [[kx, 0], [0, ky], [-kx, 0], [0, -ky]],
        "c": True
    }

def rect_path(cx: float, cy: float, w: float, h: float, r: float = 0) -> dict:
    r = min(r, w/2, h/2)
    x0, x1 = cx - w/2, cx + w/2
    y0, y1 = cy - h/2, cy + h/2
    k = KAPPA * r
    if r == 0:
        return {"v": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
                "i": [[0,0], [0,0], [0,0], [0,0]], "o": [[0,0], [0,0], [0,0], [0,0]], "c": True}
    return {
        "v": [[x0+r, y0], [x1-r, y0], [x1, y0+r], [x1, y1-r],
              [x1-r, y1], [x0+r, y1], [x0, y1-r], [x0, y0+r]],
        "i": [[0,0], [-k,0], [0,-k], [0,0], [0,0], [k,0], [0,k], [0,0]],
        "o": [[k,0], [0,0], [0,0], [0,k], [-k,0], [0,0], [0,0], [0,-k]],
        "c": True
    }

def star_path(cx: float, cy: float, ro: float, ri: float, n: int=5) -> dict:
    v, i, o = [], [], []
    for j in range(n*2):
        a = math.pi * j / n - math.pi / 2
        r = ro if j%2==0 else ri
        v.append([cx + r*math.cos(a), cy + r*math.sin(a)])
        i.append([0,0]); o.append([0,0])
    return {"v": v, "i": i, "o": o, "c": True}

# ---------------------------------------------------------------------------
# 로티 빌더
# ---------------------------------------------------------------------------
def static(v):
    return {"a": 0, "k": v}

def animated(kfs):
    return {"a": 1, "k": kfs}

def kf(t: int, s, e=None, ease="smooth") -> dict:
    if type(s) not in (list, tuple): s = [s]
    frame = {"t": t, "s": s}
    if e is not None:
        if type(e) not in (list, tuple): e = [e]
        frame["e"] = e
        if ease == "smooth":
            frame["i"] = {"x": [0.33], "y": [1]}
            frame["o"] = {"x": [0.33], "y": [0]}
        elif ease == "linear":
            frame["i"] = {"x": [0], "y": [0]}
            frame["o"] = {"x": [1], "y": [1]}
    return frame

def make_oscillation(n_frames, max_t, val1, val2, ease="smooth"):
    kfs = []
    for t in range(0, max_t + 1, n_frames):
        idx = t // n_frames
        vs = val1 if idx%2==0 else val2
        ve = val2 if idx%2==0 else val1
        
        if type(vs) not in (list, tuple): vs = [vs]
        
        if t == max_t:
            kfs.append({"t": t, "s": vs})
        else:
            kfs.append(kf(t, vs, ve, ease))
    if kfs[-1]["t"] < max_t:
        kfs.append({"t": max_t, "s": kfs[-1]["e"]})
    return kfs

def path_shape(pd):   return {"ty": "sh", "nm": "path", "ks": static(pd)}
def fill_shape(c, o=100): return {"ty": "fl", "nm": "fill", "c": static(c), "o": static(o), "r": 1}

def shape_group(nm, items):
    return {
        "ty": "gr", "nm": nm,
        "it": items + [{
            "ty": "tr", "p": static([0,0]), "a": static([0,0]),
            "s": static([100,100]), "r": static(0), "o": static(100), "sk": static(0), "sa": static(0)
        }]
    }

def solid_oval(nm, cx, cy, rx, ry, col):
    return shape_group(nm, [path_shape(oval_path(cx, cy, rx, ry)), fill_shape(col)])

def solid_rect(nm, cx, cy, w, h, r, col):
    return shape_group(nm, [path_shape(rect_path(cx, cy, w, h, r)), fill_shape(col)])

def layer_base(ind: int, nm: str, op: int, px=0, py=0,
               pos_kf=None, rot_kf=None, scl_kf=None, opa_kf=None, parent: int=None) -> dict:
    lyr = {
        "ind": ind, "ty": 4, "nm": nm,
        "ip": 0, "op": op, "st": 0, "bm": 0,
        "ks": {
            "p": animated(pos_kf) if pos_kf else static([px, py, 0]),
            "a": static([0, 0, 0]),
            "s": animated(scl_kf) if scl_kf else static([100, 100, 100]),
            "r": animated(rot_kf) if rot_kf else static(0),
            "o": animated(opa_kf) if opa_kf else static(100),
        },
        "shapes": []
    }
    if parent is not None:
        lyr["parent"] = parent
    return lyr

def bg_circle_layer(ind: int, op: int, color: list) -> dict:
    layer = layer_base(ind, "bg_circle", op, px=100, py=100)
    layer["shapes"] = [shape_group("bg", [path_shape(oval_path(0, 0, 90, 90)), fill_shape(color)])]
    return layer

def lottie_doc(nm: str, fr: int, op: int, layers: list) -> dict:
    return {
        "v": "5.5.7", "meta": {"g": "make_dog_lottie.py"},
        "fr": fr, "ip": 0, "op": op, "w": 200, "h": 200, "nm": nm, "ddd": 0,
        "assets": [], "layers": layers
    }

# ---------------------------------------------------------------------------
# 고품질 캐릭터 부위 그리기
# ---------------------------------------------------------------------------
def shadow_shapes():
    return [solid_oval("shadow", 0, 0, 55, 10, SHADOW_COLOR)]

def tail_shapes():
    return [solid_oval("tail", 0, -18, 10, 28, TAIL_COLOR)]

def leg_shapes():
    DARK_LEG = [0.75, 0.45, 0.15, 1]
    return [
        # 뒷다리
        solid_rect("leg_rl", -18, 8, 12, 22, 6, DARK_LEG),
        solid_rect("leg_rr", 18, 8, 12, 22, 6, DARK_LEG),
        # 앞다리
        solid_rect("leg_fl", -10, 14, 14, 25, 6, BODY_COLOR),
        solid_rect("leg_fr", 10, 14, 14, 25, 6, BODY_COLOR),
        # 앞발
        solid_oval("paw_fl", -10, 25, 16, 8, BELLY_COLOR),
        solid_oval("paw_fr", 10, 25, 16, 8, BELLY_COLOR),
    ]

def body_shapes():
    return [
        solid_oval("body_main", 0, 5, 45, 38, BODY_COLOR),
        solid_oval("body_belly", 0, 16, 30, 24, BELLY_COLOR),
    ]

def collar_shapes():
    return [
        solid_rect("collar_band", 0, -2, 38, 8, 4, COLLAR_COLOR),
        solid_oval("tag", 0, 5, 6, 6, TAG_COLOR),
        solid_oval("tag_hole", 0, 3, 1.5, 1.5, [0,0,0,0.5]),
    ]

def head_shapes():
    return [
        solid_oval("head_base", 0, 0, 42, 36, BODY_COLOR),
        solid_oval("spot", -16, -6, 15, 18, SPOT_COLOR),
    ]

def ear_shapes():
    return [solid_oval("ear", 0, 0, 12, 24, EAR_COLOR)]

def face_shapes(eye_squint, eye_close):
    shapes = [
        # 주둥이와 코
        solid_oval("muzzle", 0, 10, 22, 14, BELLY_COLOR),
        solid_oval("nose", 0, 4, 9, 6, NOSE_COLOR),
        solid_oval("nose_shine", -2, 3, 2, 1, WHITE),
    ]
    if eye_close:
        shapes.append(solid_rect("left_eye",  -16, -2, 10, 2, 1, EYE_COLOR))
        shapes.append(solid_rect("right_eye",  16, -2, 10, 2, 1, EYE_COLOR))
    elif eye_squint:
        shapes.append(solid_oval("left_eye",  -16, -4, 6, 2, EYE_COLOR))
        shapes.append(solid_oval("right_eye",  16, -4, 6, 2, EYE_COLOR))
    else:
        shapes.append(solid_oval("left_eye",  -16, -4, 5, 8, EYE_COLOR))
        shapes.append(solid_oval("right_eye",  16, -4, 5, 8, EYE_COLOR))
        shapes.append(solid_oval("left_hi",  -18, -6, 2, 3, WHITE))
        shapes.append(solid_oval("right_hi",  14, -6, 2, 3, WHITE))
    return shapes

def tongue_shapes():
    # 혀
    return [solid_oval("tongue", 0, 18, 8, 14, TONGUE_COLOR)]

# ---------------------------------------------------------------------------
# 중앙 장면 코디네이터
# ---------------------------------------------------------------------------
def make_dog_scene(name, op, bg_color, anim_type):
    layers = []
    layers.append(bg_circle_layer(99, op, bg_color))
    
    face_sq = False; face_cl = False; show_tongue = False
    shadow_scale = [kf(0, [100,100,100])]
    tail_rot = [kf(0, [0])]
    body_pos = [kf(0, [100, 115, 0])]
    head_pos = [kf(0, [100, 68, 0])]
    leg_pos = [kf(0, [100, 115, 0])]
    ear_rot = None
    blink_scale = None

    if anim_type == "idle":
        # 숨쉬며 가볍게 보빙, 눈깜박임
        shadow_scale = make_oscillation(45, op, [100,100,100], [95,95,100])
        tail_rot = make_oscillation(15, op, 30, -10)
        body_pos = make_oscillation(45, op, [100, 115, 0], [100, 118, 0])
        head_pos = make_oscillation(45, op, [100, 68, 0], [100, 72, 0])
        blink_scale = [kf(0, [100,100,100]), kf(60, [100,100,100], [100,10,100], "linear"), 
                       kf(64, [100,10,100], [100,100,100], "linear"), {"t": op, "s": [100,100,100]}]

    elif anim_type == "working":
        # 헤헥거리며 집중(작업)
        face_sq = True; show_tongue = True
        tail_rot = make_oscillation(6, op, 45, -15)
        leg_pos = make_oscillation(4, op, [100, 115, 0], [100, 110, 0])
        
        # 혀만 별도 헐떡임
        t_scl = make_oscillation(4, op, [100,100,100], [100,120,100])
        t_lyr = layer_base(4, "tongue", op, px=0, py=0, scl_kf=t_scl, parent=8)
        t_lyr["ks"]["a"] = static([0, 16, 0])
        t_lyr["shapes"] = tongue_shapes()
        layers.append(t_lyr)

    elif anim_type == "alert":
        # 깜짝 놀라며 귀가 쫑긋 서고 알림표시!
        head_pos = [kf(0, [100, 68, 0], [100, 58, 0]), kf(8, [100, 58, 0], [100, 63, 0]), {"t":op, "s": [100,63,0]}]
        ear_rot = [kf(0, [0], [50]), kf(8, [50], [40]), {"t":op, "s": [40]}]
        
        # 빨간색 느낌표 팝업
        alert_scale = [
            kf(0,  [0,0,100], [130,130,100]), kf(7,  [130,130,100], [90,90,100]),
            kf(11, [90,90,100], [110,110,100]), kf(15, [110,110,100], [100,100,100]),
            kf(35, [100,100,100], [0,0,100]), {"t": op, "s": [0,0,100]}
        ]
        ring = layer_base(2, "alert_ring", op, px=145, py=35, scl_kf=alert_scale)
        ring["shapes"] = [
            shape_group("ring", [path_shape(oval_path(0, 0, 18, 18)), fill_shape(ALERT_RED)]),
            shape_group("bar", [path_shape(rect_path(0, -3, 5, 12, 2)), fill_shape(WHITE)]),
            shape_group("dot", [path_shape(oval_path(0, 7, 2.5, 2.5)), fill_shape(WHITE)]),
        ]
        layers.append(ring)

    elif anim_type == "celebrate":
        # 펄쩍펄쩍 점프 + 별 파티클
        face_sq = True; show_tongue = True
        jy = -35
        body_pos = make_oscillation(15, op, [100, 115, 0], [100, 115+jy, 0])
        head_pos = make_oscillation(15, op, [100,  68, 0], [100,  68+jy, 0])
        leg_pos  = make_oscillation(15, op, [100, 115, 0], [100, 115+jy, 0])
        
        shadow_scale = make_oscillation(15, op, [100,100,100], [40,40,100])
        tail_rot = make_oscillation(5, op, 45, -20)
        ear_rot = make_oscillation(15, op, [0], [60])
        
        # 별 생성
        def mk_star(ind, cx, cy, dly, clr):
            ap = min(dly + 6, op - 1)
            fd = min(dly + 22, op)
            o_kf = [kf(max(0, dly-1), [0], [100], "linear"), kf(ap, [100], [100], "linear"), 
                    kf(fd-4, [100], [0], "linear"), {"t": fd, "s": [0]}]
            s_kf = [kf(max(0, dly-1), [0,0,100], [120,120,100]), kf(ap, [120,120,100], [80,80,100]), 
                    kf(ap+4, [80,80,100], [100,100,100]), {"t": op, "s": [0,0,100]}]
            lyr = layer_base(ind, "star", op, px=cx, py=cy, scl_kf=s_kf, opa_kf=o_kf)
            lyr["shapes"] = [shape_group("star", [path_shape(star_path(0,0,12,5,5)), fill_shape(clr)])]
            return lyr
        
        layers.extend([
            mk_star(3,  55, 55,  0, STAR_YELLOW), mk_star(3, 142, 42,  8, STAR_YELLOW),
            mk_star(3, 155, 85, 16, STAR_PINK),   mk_star(3,  38, 82, 22, STAR_YELLOW),
            mk_star(3, 100, 15,  4, STAR_PINK),
        ])
        
        # 혀 고정
        t_lyr = layer_base(4, "tongue", op, px=0, py=0, parent=8)
        t_lyr["shapes"] = tongue_shapes()
        layers.append(t_lyr)

    elif anim_type == "sleeping":
        face_cl = True
        body_pos = make_oscillation(60, op, [100, 115, 0], [100, 118, 0])
        head_pos = make_oscillation(60, op, [100,  68, 0], [100,  72, 0])
        shadow_scale = make_oscillation(60, op, [100,100,100], [95,95,100])
        tail_rot = [kf(0, [10])]

    # --- 공통 레이어 빌드 ---
    shadow = layer_base(20, "shadow", op, px=100, py=148, scl_kf=shadow_scale)
    shadow["shapes"] = shadow_shapes()
    layers.append(shadow)

    tail = layer_base(15, "tail", op, px=135, py=110, rot_kf=tail_rot)
    tail["ks"]["a"] = static([0, 10, 0])
    tail["shapes"] = tail_shapes()
    layers.append(tail)

    legs = layer_base(12, "legs", op, pos_kf=leg_pos)
    legs["shapes"] = leg_shapes()
    layers.append(legs)

    body = layer_base(10, "body", op, pos_kf=body_pos)
    body["shapes"] = body_shapes()
    layers.append(body)

    # 헤드 중심점
    head = layer_base(8, "head", op, pos_kf=head_pos)
    head["shapes"] = head_shapes()
    layers.append(head)

    # 목줄 (head를 따라가도록 parent=8)
    collar = layer_base(9, "collar", op, px=0, py=26, parent=8)
    collar["shapes"] = collar_shapes()
    layers.append(collar)

    l_ear_rot, r_ear_rot = None, None
    if ear_rot:
        l_ear_rot = []
        r_ear_rot = []
        for f in ear_rot:
            lf = f.copy(); rf = f.copy(); lf["s"] = [f["s"][0]]; rf["s"] = [-f["s"][0]]
            if "e" in f: lf["e"] = [f["e"][0]]; rf["e"] = [-f["e"][0]]
            l_ear_rot.append(lf); r_ear_rot.append(rf)

    left_ear = layer_base(7, "left_ear", op, px=-18, py=-8, rot_kf=l_ear_rot, parent=8)
    left_ear["ks"]["a"] = static([0, -12, 0])
    left_ear["shapes"] = ear_shapes()
    
    right_ear = layer_base(6, "right_ear", op, px=18, py=-8, rot_kf=r_ear_rot, parent=8)
    right_ear["ks"]["a"] = static([0, -12, 0])
    right_ear["shapes"] = ear_shapes()
    
    layers.extend([left_ear, right_ear])

    face_layer = layer_base(5, "face", op, px=0, py=0, scl_kf=blink_scale, parent=8)
    face_layer["shapes"] = face_shapes(face_sq, face_cl)
    layers.append(face_layer)

    # 앞서 배치된 레이어들을 ind 숫자에 맞춰 정렬 (낮을수록 위로 렌더링하도록 의도했으나, 
    # Lottie는 배열 아래로 갈수록 상단에 렌더링되므로, 순서를 반대로 정렬하거나 인덱스를 역순으로 설정)
    # Lottie Docs: 배열의 뒤쪽 요소일수록 나중에 그려짐 => 위에 보임.
    # 우리의 레이어 인덱스: Shadow 20, Tail 15, Legs 12, Body 10, Collar 9, Head 8, Ears 7, Face 5, BG 99
    # => 큰 인덱스부터 정렬 (역순) 하면 작은 값이 배열 뒤쪽으로 가므로, Face(5)가 맨 마지막 => 맨 위에 렌더링됨!
    layers.sort(key=lambda x: x["ind"], reverse=True)
    
    fname = f"puppy_{anim_type}" 
    if anim_type == "sleeping": fname = "Puppy sleeping" # 예외처리 원본 유지용
    return lottie_doc(fname, 30, op, layers)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="data")
    args = parser.parse_args()
    
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    
    scenes = [
        ("puppy_idle.json",      90,  "idle"),
        ("puppy_working.json",   60,  "working"),
        ("puppy_alert.json",     45,  "alert"),
        ("puppy_celebrate.json", 75,  "celebrate"),
        ("Puppy sleeping.json",  120, "sleeping")
    ]
    
    for fname, op, atype in scenes:
        doc = make_dog_scene(fname, op, globals()[f"BG_{atype.upper()}"], atype)
        path = out / fname
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] {path}  ({doc['op']}프레임)")

    print("\n완료! 실행: python desktop-app/launch.py --lottie")

if __name__ == "__main__":
    main()
