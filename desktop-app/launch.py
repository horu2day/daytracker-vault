"""
desktop-app/launch.py - Quick launcher for the DayTracker character agent.

Can be double-clicked or called from the command line.
Automatically sets PYTHONPATH so that scripts.config is importable.

Usage:
    python desktop-app/launch.py              # emoji 버전 (기본)
    python desktop-app/launch.py --lottie     # Lottie 강아지 애니메이션 버전
    python desktop-app/launch.py --project-root C:/MYCLAUDE_PROJECT/daytracker-vault
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make sure the project root is on sys.path for scripts.* imports
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent  # daytracker-vault/
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_HERE))
os.environ.setdefault("DAYTRACKER_ROOT", str(_ROOT))

if __name__ == "__main__":
    # --lottie 플래그 처리 (main() 함수에 전달하기 전에 제거)
    use_lottie = "--lottie" in sys.argv
    if use_lottie:
        sys.argv.remove("--lottie")

    if use_lottie:
        from character_lottie import main  # noqa: E402
    else:
        from character_pyqt import main  # noqa: E402

    sys.exit(main())
