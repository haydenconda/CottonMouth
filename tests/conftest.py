import sys
from pathlib import Path

# Make the dependency-free SDK importable without installing it.
_ROOT = Path(__file__).resolve().parents[1]
_SDK_SRC = _ROOT / "sdk" / "src"
for _p in (_SDK_SRC, _ROOT):  # _ROOT so backend `src.*` modules import in tests
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
