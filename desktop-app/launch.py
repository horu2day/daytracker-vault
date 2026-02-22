"""
desktop-app/launch.py - Quick launcher for the DayTracker character agent.

Can be double-clicked or called from the command line.
Automatically sets PYTHONPATH so that scripts.config is importable.

Usage:
    python desktop-app/launch.py
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
os.environ.setdefault("DAYTRACKER_ROOT", str(_ROOT))

# Now launch the character
from character_pyqt import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
