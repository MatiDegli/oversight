"""Lets the graph tests import their shared helpers: pytest's importlib mode adds no test folder
to sys.path."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
