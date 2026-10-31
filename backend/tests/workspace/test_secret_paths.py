"""Obvious secret names, SSH identity boundaries and committed examples."""

import pytest

from oversight.workspace import is_secret_path


@pytest.mark.parametrize(
    ("path", "secret"),
    [
        (".env", True),
        (".ENV", True),
        (".Env.Local", True),
        ("sub/dir/.env.local", True),
        ("cert.pem", True),
        ("CERT.PEM", True),
        ("server.KEY", True),
        ("deploy/server.key", True),
        ("id_rsa", True),
        ("ID_RSA", True),
        ("id_ed25519", True),
        ("id_ed25519.pub", True),
        (".ssh/id_ecdsa", True),
        ("id_rsa_work", True),
        ("id_dsa", True),
        ("id_ed25519_sk", True),
        ("id_rsa-work", True),
        ("id_rsa_loader.py", True),
        (".envrc", True),
        (".env.example", False),
        (".ENV.EXAMPLE", False),
        ("sub/.env.local.example", False),
        ("cert.pem.example", False),
        ("server.key.example", False),
        ("id_rsa.example", False),
        ("keys.py", False),
        ("monkey.py", False),
        ("README.md", False),
        ("id_utils.py", False),
        ("id_generator.py", False),
        ("identity.py", False),
        ("environment.py", False),
        ("id_rsa2", False),
        ("id_ed25519loader.py", False),
        (".env/ordinary.py", False),
        ("", False),
    ],
)
def test_secret_path_names(path: str, secret: bool) -> None:
    assert is_secret_path(path) is secret
