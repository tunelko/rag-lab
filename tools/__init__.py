"""Local maintenance tools; none run automatically on import."""
import sys
from pathlib import Path

# The server code and the installer live in their own folders and import each other by bare name.
for _folder in ('install', 'app'):
    _path = str(Path(__file__).resolve().parents[1] / _folder)
    if _path not in sys.path:
        sys.path.insert(0, _path)
