"""Obvious secret names, SSH identity boundaries and committed examples."""

import pytest

from oversight.workspace import SECRET_FILE_REFUSAL, is_secret_path, secret_refusal


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
        (".env/ordinary.py", True),
        (".env/", True),
        (".envs/.production/.django", True),
        ("sub/.ENV/x.py", True),
        (".envs/app.example", True),
        (".venv/lib/x.py", False),
        ("env/x.py", False),
        ("x.env/y.py", False),
        ("sub/", False),  # Empty basename pins the no-raise behavior.
        ("", False),
    ],
)
def test_secret_path_names(path: str, secret: bool) -> None:
    assert is_secret_path(path) is secret


@pytest.mark.parametrize("path", ["../.env", "sub/" + ".env" + "x" * 100])
def test_secret_refusal_basename_and_bound(path: str) -> None:
    message = secret_refusal(path)
    assert message == SECRET_FILE_REFUSAL + path.rsplit("/", 1)[-1][:60]
    assert "/" not in message[len(SECRET_FILE_REFUSAL) :]
