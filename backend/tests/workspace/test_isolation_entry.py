"""The trusted entry programs reject missing isolation before releasing any test code."""

import builtins
import errno
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from oversight.workspace import isolation as iso
from oversight.workspace.isolation_entry import HELPER, LAUNCHER


class Executed(Exception):
    pass


@pytest.mark.parametrize(
    "broken",
    [None, "pid", "net", "pidns", "mountns", "procdev", "mount", "shared", "visible", "connect"],
)
def test_helper_checks_private_proc_and_closes_pipe_before_exec(broken: str | None) -> None:
    writes: list[bytes] = []
    closed: list[int] = []
    seen: list[str] = []
    identities = {"net": "new-net", "pid": "new-pid", "mnt": "new-mnt"}
    if broken in ("net", "pidns", "mountns"):
        identities[{"net": "net", "pidns": "pid", "mountns": "mnt"}[broken]] = "old"
    device = 200 if broken != "procdev" else 100
    mount = "10 9 0:200 / /proc rw - proc proc rw"
    if broken == "mount":
        mount = "10 9 0:200 / /proc rw - ext4 /dev/x rw"
    if broken == "shared":
        mount = "10 9 0:200 / /proc rw shared:5 - proc proc rw"

    def lexists(path: str) -> bool:
        seen.append(path)
        return broken == "visible" and path.endswith("/environ")

    def execute(path: str, argv: list[str]) -> None:
        assert closed == [10, 11]
        assert path == "/demo/python" and argv == [path, "-m", "pytest"]
        assert len(writes) == 1
        raise Executed

    fake_os = SimpleNamespace(
        getpid=lambda: 2 if broken == "pid" else 1,
        readlink=lambda path: identities[path.rsplit("/", 1)[-1]],
        stat=lambda _: SimpleNamespace(st_dev=device),
        major=lambda _: 0,
        minor=lambda n: n,
        path=SimpleNamespace(lexists=lexists),
        write=lambda fd, data: writes.append(data),
        close=closed.append,
        read=lambda fd, count: b"1",
        execv=execute,
    )

    class Client:
        def __enter__(self) -> "Client":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def settimeout(self, timeout: float) -> None:
            pass

        def connect_ex(self, address: object) -> int:
            return 0 if broken == "connect" else errno.ECONNREFUSED

    def fake_path(_: str) -> SimpleNamespace:
        return SimpleNamespace(read_text=lambda: mount)

    with pytest.raises(Executed if broken is None else AssertionError):
        exec(HELPER, helper_globals(fake_os, fake_path, Client))
    if broken is None:
        assert json.loads(writes[0])["pid"] == 1
        assert seen == [f"/proc/999/{name}" for name in ("cmdline", "cwd", "fd", "maps", "environ")]
    else:
        assert writes == [] and closed == []


def helper_globals(fake_os: Any, fake_path: Any, client: Any) -> dict[str, Any]:
    modules = {
        "os": fake_os,
        "sys": SimpleNamespace(
            argv=[
                "-c",
                "10",
                "11",
                "nonce",
                "999",
                "old",
                "old",
                "old",
                "100",
                "marker",
                "1234",
                "/demo/python",
                "-m",
                "pytest",
            ]
        ),
        "pathlib": SimpleNamespace(Path=fake_path),
        "socket": SimpleNamespace(socket=client),
    }
    original = builtins.__import__

    def importing(name: str, *args: Any, **kwargs: Any) -> Any:
        return modules[name] if name in modules else original(name, *args, **kwargs)

    return {"__builtins__": vars(builtins) | {"__import__": importing}}


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["parent_race", "setuid", "capabilities", "credentials"])
async def test_launcher_parent_race_and_unprivileged_exec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    binary = sys.executable
    parent = os.getpid() + (1 if kind == "parent_race" else 0)
    prefix = ""
    if kind == "setuid":
        target = tmp_path / "setuid"
        target.write_text("never execute")
        target.chmod(0o4755)
        binary = str(target)
    if kind == "capabilities":
        prefix = "import os; os.getxattr = lambda *args: b'capabilities'\n"
    command = (
        "import os,json; print(json.dumps([os.getuid(),os.geteuid(),os.getgid(),os.getegid()]))"
    )
    monkeypatch.setattr(
        iso,
        "_launch",
        lambda *_: [
            sys.executable,
            "-I",
            "-S",
            "-c",
            prefix + LAUNCHER,
            str(parent),
            binary,
            "-c",
            command,
        ],
    )
    runner = iso.IsolatedRunner(tmp_path)
    result = await runner._run(["true"], tmp_path, 5, None, 1000, probe=True)
    if kind == "credentials":
        assert result.returncode == 0
        assert json.loads(result.stdout) == [os.getuid(), os.geteuid(), os.getgid(), os.getegid()]
    else:
        assert result.returncode == 125 and result.stdout == ""
