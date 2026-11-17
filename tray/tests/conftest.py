import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tray"), str(ROOT / "common")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Merker wie „Hinweis gezeigt“ nie ins echte Profil schreiben
import tempfile  # noqa: E402
_cfg = tempfile.mkdtemp(prefix="kidctl-tray-test-")
os.environ["XDG_CONFIG_HOME"] = _cfg
os.environ["APPDATA"] = _cfg
