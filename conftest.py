"""Root-level conftest so `tests/` can import `src.*` when pytest runs
from D:/ghost. Adds the project root to sys.path if not already there."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
