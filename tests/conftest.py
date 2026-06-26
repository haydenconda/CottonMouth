import sys
from pathlib import Path

# Make the dependency-free SDK importable without installing it.
_SDK_SRC = Path(__file__).resolve().parents[1] / "sdk" / "src"
if str(_SDK_SRC) not in sys.path:
    sys.path.insert(0, str(_SDK_SRC))
