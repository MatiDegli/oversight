"""Name-only secret-file policy for tools that expose file contents to models."""

import sys

from oversight.workspace.errors import InvalidPath
from oversight.workspace.files import nt_ambiguous, split_relative

# Maintainer decision (2026-10-31): narrow ARCHITECTURE.md's id_* to SSH identity names
# so id_utils.py stays readable. See DECISIONS.md,
# “Refuse obvious secret files at model tool boundaries”.
SSH_IDENTITY_NAMES = ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")
SECRET_FILE_REFUSAL = "Cannot access .env*, *.pem, *.key and SSH private-key files: "


def secret_refusal(path: str) -> str:
    if sys.platform == "win32" and any(nt_ambiguous(part) for part in path.split("/")):
        try:
            split_relative(path, nt=True)
        except InvalidPath as error:
            return str(error)
    return SECRET_FILE_REFUSAL + path.rsplit("/", 1)[-1][:60]


def is_secret_path(path: str, *, nt: bool | None = None) -> bool:
    """Check directory names and the basename without inspecting the filesystem."""
    if sys.platform == "win32" if nt is None else nt:
        return is_secret_path(path, nt=False) or any(
            nt_ambiguous(part)
            or is_secret_path(part, nt=False)
            or is_secret_path(part.upper(), nt=False)
            for part in path.split("/")
        )
    *directories, basename = path.split("/")
    if any(segment.casefold().startswith(".env") for segment in directories):
        return True
    name = basename.casefold()
    if name.endswith(".example"):
        return False
    if name.startswith(".env") or name.endswith((".pem", ".key")):
        return True
    return any(
        name == identity or (name.startswith(identity) and name[len(identity)] in "_.-")
        for identity in SSH_IDENTITY_NAMES
    )
