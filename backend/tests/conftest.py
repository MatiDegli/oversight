"""pytest's importlib mode adds no test folder to sys.path, so this file adds this one, for the
live server helpers the API, end-to-end and app tests share."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
