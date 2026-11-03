"""Frozen person-owned startup configuration; opening grants no executable authority."""

import hashlib
import hmac
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from oversight.workspace.preview_launch_spec import PreviewLaunchSpec
from oversight.workspace.source import OVERSIGHT_SELF, SourceInventory

_AUTHORITY_KEY = os.urandom(32)


@dataclass(frozen=True)
class ReviewedAppAuthorization:
    inventory: SourceInventory
    launch: PreviewLaunchSpec
    proof: bytes = field(default=b"", repr=False)

    def _signature(self) -> bytes:
        return hmac.digest(_AUTHORITY_KEY, repr((self.inventory, self.launch)).encode(), "sha256")

    def validate(self, inventory: SourceInventory | None = None) -> None:
        try:
            if (
                not hmac.compare_digest(self.proof, self._signature())
                or self.inventory.commit != OVERSIGHT_SELF.commit
                or (inventory is not None and inventory != self.inventory)
            ):
                raise ValueError("preview app is not authorized")
            self.inventory.verify()
            self.launch.verify()
        except (ValueError, OSError, RuntimeError):
            raise ValueError("preview app is not authorized") from None


def authorize_reviewed_app(
    inventory: SourceInventory, launch: PreviewLaunchSpec
) -> ReviewedAppAuthorization:
    """Mint only after verified self selection; names/enabled flags cannot mint a proof."""
    try:
        if (
            inventory.commit != OVERSIGHT_SELF.commit
            or launch.mode != "static-api"
            or launch.original != inventory.root
        ):
            raise ValueError("preview app is not authorized")
        inventory.verify()
        launch.verify()
        unsigned = ReviewedAppAuthorization(inventory, launch)
        return ReviewedAppAuthorization(inventory, launch, unsigned._signature())  # pyright: ignore[reportPrivateUsage]
    except (ValueError, OSError, RuntimeError):
        raise ValueError("preview app is not authorized") from None


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
    lease_s: float = 600
    lifetime_s: float = 900
    readiness_s: float = 30
    memory_limit_bytes: int = 536870912
    build_memory_bytes: int = 2 * 1024**3
    build_cores: int = 2
    nested_memory_bytes: int = 1024**3
    nested_cores: int = 1
    node_heap_mib: int = 1536
    output_file_bytes: int = 16 * 1024**2
    output_total_bytes: int = 256 * 1024**2
    output_files: int = 10_000
    cache_bytes: int = 2 * 1024**3
    diagnostics_bytes: int = 1024**2
    queue_cap: int = 8
    enabled: bool = False
    reviewed_app: ReviewedAppAuthorization | None = None

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("preview enabled flag must be boolean")

    @property
    def configured(self) -> bool:
        return bool(
            self.reviewed_app or (self.adapter and self.interpreter and self.ports and self.origins)
        )

    @property
    def production_open(self) -> bool:
        return self.enabled

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(repr(self).encode()).hexdigest()

    def validate(self) -> None:
        """Partial/invalid configuration refuses execution, never supplies a fallback."""
        if self.reviewed_app is not None:
            self.reviewed_app.validate()
            launch = self.reviewed_app.launch
            if (
                not self.enabled
                or self.trusted_demo
                or self.interpreter != launch.resources[0]
                or launch.api_port not in self.ports
                or launch.public_port not in self.ports
            ):
                raise ValueError("preview app is not authorized")
        else:
            if not self.enabled or not self.configured or not self.trusted_demo:
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
        resources = (
            self.memory_limit_bytes,
            self.build_memory_bytes,
            self.build_cores,
            self.nested_memory_bytes,
            self.nested_cores,
            self.node_heap_mib,
            self.output_file_bytes,
            self.output_total_bytes,
            self.output_files,
            self.cache_bytes,
            self.diagnostics_bytes,
            self.queue_cap,
        )
        if any(type(value) is not int or value <= 0 for value in resources):
            raise ValueError("invalid preview limits")
        if self.reviewed_app is None:
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
