"""The widget folder is not a package: put it on the path so the tests can
``import server`` however pytest is started."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
