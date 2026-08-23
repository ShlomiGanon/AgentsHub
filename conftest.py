"""Ensures the repo root is on sys.path regardless of how pytest is invoked,
so `import persistence.interface`, `import tests.helpers`, etc. resolve the
same way whether run as `pytest` or `python -m pytest`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
