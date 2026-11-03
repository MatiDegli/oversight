"""Frozen person-owned startup configuration. There is no production opening switch."""

import hashlib
import math
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class PreviewProfile:
    adapter: Path | None = None
    interpreter: Path | None = None
    ports: tuple[int, ...] = ()
    origins: tuple[str, ...] = ()
    backend_port: int = 8710
    vite_port: int | None = None
    trusted_demo: bool = False
    heartbeat_s: float = 10
    lease_s: float = 30
    lifetime_s: float = 900
    readiness_s: float = 10
    memory_limit_bytes: int = 536870912

    @property
    def configured(self) -> bool:
        return bool(self.adapter and self.interpreter and self.ports and self.origins)

    @property
    def production_open(self) -> bool:
        return False

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(repr(self).encode()).hexdigest()

    def validate(self) -> None:
        """Partial/invalid configuration refuses execution, never supplies a fallback."""
        if not self.configured or not self.trusted_demo:
            raise ValueError("preview profile is disabled")
        assert self.adapter is not None and self.interpreter is not None
        expected = Path(__file__).resolve().parents[1] / "preview_adapter.py"
        if not self.adapter.is_absolute() or self.adapter != expected:
            raise ValueError("preview adapter must be the trusted absolute adapter")
        # Do not resolve the interpreter symlink: its venv path is its identity.
        if not self.interpreter.is_absolute():
            raise ValueError("preview interpreter must be absolute")
        if len(set(self.ports)) != len(self.ports) or any(
            type(port) is not int
            or not 1 <= port <= 65535
            or port in (self.backend_port, self.vite_port)
            for port in self.ports
        ):
            raise ValueError("invalid preview port pool")
        for origin in self.origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme != "http"
                or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
                or (parsed.port or 80) not in {self.backend_port, self.vite_port}
                or any(char.isspace() for char in origin)
                or origin != f"http://{parsed.netloc}"
            ):
                raise ValueError("invalid preview origin")
        values = (self.heartbeat_s, self.lease_s, self.lifetime_s, self.readiness_s)
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise ValueError("invalid preview deadline")
        if self.heartbeat_s >= self.lease_s or type(self.memory_limit_bytes) is not int:
            raise ValueError("invalid preview limits")
        if self.memory_limit_bytes <= 0:
            raise ValueError("invalid preview limits")
        self.validate_files()

    def validate_files(self) -> None:
        """A missing adapter/interpreter disables execution before spawning any supervisor."""
        assert self.adapter is not None and self.interpreter is not None
        if (
            not self.adapter.is_file()
            or self.adapter.resolve() != self.adapter
            or not self.interpreter.is_file()
            or not os.access(self.interpreter, os.X_OK)
        ):
            raise ValueError("preview executables are unavailable")
