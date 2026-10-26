"""Configuration from the environment and the .env file. Read by app.py and the API probe."""

import os
from dataclasses import dataclass, field
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
    runtime_dir: Path = REPO_ROOT / ".runtime"
    """Run-time state, gitignored: `runs/{run_id}.sqlite`, `replays/{run_id}.sqlite`, and so on."""
    layout: Path = REPO_ROOT / "fixtures" / "layouts" / "demo-repo.json"
    """The preset layout, `{node_id: {x, y}}`; a missing file means the graph has no positions."""
    # The provider keys. repr=False keeps them out of any log line or traceback that shows Settings.
    meta_api_key: str | None = field(default=None, repr=False)
    baseten_api_key: str | None = field(default=None, repr=False)

    @classmethod
    def load(cls, env_file: Path = REPO_ROOT / ".env") -> "Settings":
        """The process environment wins over the .env file."""
        merged = read_env_file(env_file) | dict(os.environ)
        return cls(
            host=merged.get("OVERSIGHT_HOST", cls.host),
            port=int(merged.get("OVERSIGHT_PORT", cls.port)),
            runtime_dir=Path(merged.get("OVERSIGHT_RUNTIME_DIR", cls.runtime_dir)),
            layout=Path(merged.get("OVERSIGHT_LAYOUT") or cls.layout),
            # An empty value, as in .env.example, means the key is not set.
            meta_api_key=merged.get("META_API_KEY") or None,
            baseten_api_key=merged.get("BASETEN_API_KEY") or None,
        )
