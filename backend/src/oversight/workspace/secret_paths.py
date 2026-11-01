"""Name-only secret-file policy for tools that expose file contents to models."""

# Maintainer decision (2026-10-31): narrow AGENTS.md's id_* to SSH identity names
# so id_utils.py stays readable. See DECISIONS.md,
# “Refuse obvious secret files at model tool boundaries”.
SSH_IDENTITY_NAMES = ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")
SECRET_FILE_REFUSAL = "Cannot access .env*, *.pem, *.key and SSH private-key files: "


def secret_refusal(path: str) -> str:
    return SECRET_FILE_REFUSAL + path.rsplit("/", 1)[-1][:60]


def is_secret_path(path: str) -> bool:
    """Check directory names and the basename without inspecting the filesystem."""
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
