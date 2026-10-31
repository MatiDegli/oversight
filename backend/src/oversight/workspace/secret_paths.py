"""Name-only secret-file policy for tools that expose file contents to models."""

SSH_IDENTITY_NAMES = ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")
SECRET_FILE_REFUSAL = "Cannot access .env*, *.pem, *.key and SSH private-key files: "


def is_secret_path(path: str) -> bool:
    """Check only the case-folded basename, without inspecting the filesystem."""
    name = path.rsplit("/", 1)[-1].casefold()
    if name.endswith(".example"):
        return False
    if name.startswith(".env") or name.endswith((".pem", ".key")):
        return True
    return any(
        name == identity or (name.startswith(identity) and name[len(identity)] in "_.-")
        for identity in SSH_IDENTITY_NAMES
    )
