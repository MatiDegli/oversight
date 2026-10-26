"""Configuration from the environment and the repository's .env file. Only app.py reads it."""

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def read_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines; blank lines and comments are skipped, quotes around values removed."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("\"'")
    return values


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8710

    @classmethod
    def load(cls, env_file: Path = REPO_ROOT / ".env") -> "Settings":
        """The process environment wins over the .env file."""
        merged = read_env_file(env_file) | dict(os.environ)
        return cls(
            host=merged.get("OVERSIGHT_HOST", cls.host),
            port=int(merged.get("OVERSIGHT_PORT", cls.port)),
        )
