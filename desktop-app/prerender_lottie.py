"""
Lottie JSON → 모든 프레임을 PNG 파일로 미리 렌더링하는 유틸리티.
Git Bash에서 rlottie-python이 segfault 나는 문제를 우회하기 위해
별도 Python 프로세스(CMD/PowerShell)에서 실행.

Usage:
    python desktop-app/prerender_lottie.py <lottie.json> <output_dir> [size]
"""
import sys
import os
from pathlib import Path

def prerender(json_path: str, output_dir: str, size: int = 160):
    import rlottie_python as rl

    anim = rl.LottieAnimation.from_file(json_path)
    total = anim.lottie_animation_get_totalframe()
    os.makedirs(output_dir, exist_ok=True)

    print(f"Rendering {total} frames at {size}x{size}...")
    for i in range(total):
        # render_pillow_frame에 크기 인자를 넘기면 Segfault 발생 → PIL resize 사용
        img = anim.render_pillow_frame(i)
        if img.size != (size, size):
            img = img.resize((size, size))
        out = Path(output_dir) / f"frame_{i:04d}.png"
        img.save(str(out), "PNG")
        if i % 10 == 0:
            print(f"  {i}/{total}", flush=True)

    print(f"Done. {total} frames saved to {output_dir}")
    return total

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: prerender_lottie.py <lottie.json> <output_dir> [size]")
        sys.exit(1)
    json_path = sys.argv[1]
    output_dir = sys.argv[2]
    size = int(sys.argv[3]) if len(sys.argv) > 3 else 160
    prerender(json_path, output_dir, size)
