import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tray"), str(ROOT / "common")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
