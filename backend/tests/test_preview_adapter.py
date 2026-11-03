"""Startup order, strict provenance and exact memory-limit evidence."""

import socket

import pytest
from preview_adapter_helpers import copy_shop, launched

pytestmark = pytest.mark.anyio


async def test_output_and_limit_precede_import(tmp_path):
    root = copy_shop(tmp_path)
    (root / "shop/__init__.py").write_text("""import os, resource
assert os.readlink('/proc/self/fd/1') == '/dev/null'
assert os.readlink('/proc/self/fd/2') == '/dev/null'
assert resource.getrlimit(resource.RLIMIT_AS) == (536870912, 536870912)
os.write(1, b'candidate stdout secret')
os.write(2, b'candidate stderr secret')
""")
    async with launched(tmp_path, root=root) as child:
        assert (await child.request()).status == 200
        assert not child.protocol.output[1] and not child.protocol.output[2]


@pytest.mark.parametrize(
    "setup",
    [
        "m=types.ModuleType('shop'); m.__file__='/tmp/foreign.py'; sys.modules['shop']=m",
        "sys.modules['shop']=types.ModuleType('shop')",
        "m=types.ModuleType('shop.cached'); m.__file__=__file__; sys.modules['shop.cached']=m",
    ],
)
async def test_foreign_preloaded_and_missing_metadata(tmp_path, setup):
    async with launched(tmp_path, setup=setup, ready=False) as child:
        assert await child.exit() == 72


@pytest.mark.parametrize("kind", ["escape", "missing", "metadata"])
async def test_import_provenance(tmp_path, kind):
    root = copy_shop(tmp_path)
    if kind == "escape":
        foreign = tmp_path / "foreign.py"
        foreign.write_text("pass\n")
        (root / "shop/__init__.py").unlink()
        (root / "shop/__init__.py").symlink_to(foreign)
    else:
        (root / "shop/__init__.py").write_text(
            "__file__ = '/nonexistent/module.py'\n" if kind == "missing" else "del __file__\n"
        )
    async with launched(tmp_path, root=root, ready=False) as child:
        assert await child.exit() == 72


async def test_late_module_escape_is_fatal_before_response(tmp_path):
    root = copy_shop(tmp_path)
    with (root / "shop/cart.py").open("a") as stream:
        stream.write("""
import sys, types
def late(self):
    m = types.ModuleType('shop.late')
    m.__file__ = '/does/not/exist'
    sys.modules['shop.late'] = m
    return 1500
Cart.total = late
""")
    async with launched(tmp_path, root=root) as child:
        with pytest.raises(ValueError):
            await child.request()
        assert await child.exit() == 72


@pytest.mark.parametrize(
    "setup",
    [
        "resource.setrlimit=lambda *a: (_ for _ in ()).throw(OSError('private'))",
        "resource.setrlimit=lambda *a: None",
        "del resource.RLIMIT_AS",
    ],
)
async def test_limit_installation_must_succeed_and_verify(tmp_path, setup):
    async with launched(tmp_path, setup=setup, ready=False) as child:
        assert await child.exit() == 73


@pytest.mark.parametrize("phase", ["import", "render"])
async def test_caught_memory_error_has_private_exit(tmp_path, phase):
    root = copy_shop(tmp_path)
    path = root / ("shop/__init__.py" if phase == "import" else "shop/cart.py")
    with path.open("a") as stream:
        stream.write(
            "\nraise MemoryError('private allocation detail')\n"
            if phase == "import"
            else "\ndef exhausted(self):\n    raise MemoryError('private')\nCart.total=exhausted\n"
        )
    async with launched(tmp_path, root=root, ready=phase != "import") as child:
        if phase == "render":
            with pytest.raises(ValueError):
                await child.request()
        assert await child.exit() == 74


async def test_requested_limit_is_not_weakened(tmp_path):
    root = copy_shop(tmp_path)
    (root / "shop/__init__.py").write_text(
        "import resource\nassert resource.getrlimit(resource.RLIMIT_AS)==(268435456,268435456)\n"
    )
    async with launched(tmp_path, root=root, extra=("--memory-limit-bytes", "268435456")) as child:
        assert (await child.request()).status == 200


async def test_inherited_listener_never_binds_and_closes_on_exit(tmp_path):
    setup = "def refused(*a): raise AssertionError('bind/listen called')\n"
    setup += "socket.socket.bind=refused\nsocket.socket.listen=refused"
    async with launched(tmp_path, setup=setup) as child:
        port = child.port
        assert (await child.request()).status == 200
    # No assertion about C namespace/descendant death follows from this local check.
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", port)) != 0


@pytest.mark.parametrize("fd", [0, 1, 2])
async def test_standard_descriptors_are_refused(tmp_path, fd):
    async with launched(tmp_path, fd=fd, ready=False) as child:
        assert await child.exit() == 71


@pytest.mark.parametrize(
    "kind",
    ["closed", "file", "not_listening", "wrong_port", "wrong_address", "wrong_family", "datagram"],
)
async def test_invalid_listener(tmp_path, kind):
    sock = socket.socket(
        socket.AF_UNIX if kind == "wrong_family" else socket.AF_INET,
        socket.SOCK_DGRAM if kind == "datagram" else socket.SOCK_STREAM,
    )
    if kind not in ("wrong_family", "datagram"):
        sock.bind(("0.0.0.0" if kind == "wrong_address" else "127.0.0.1", 0))
        if kind != "not_listening":
            sock.listen()
    setup = ""
    if kind == "closed":
        setup = "os.close(int(sys.argv[sys.argv.index('--listener-fd')+1]))"
    if kind == "file":
        setup = (
            "fd=int(sys.argv[sys.argv.index('--listener-fd')+1]); "
            "f=os.open(__file__,os.O_RDONLY); os.dup2(f,fd); os.close(f)"
        )
    extra = ("--port", "1") if kind == "wrong_port" else ()
    async with launched(tmp_path, listener=sock, setup=setup, extra=extra, ready=False) as child:
        assert await child.exit() == 71


@pytest.mark.parametrize(
    "extra",
    [
        ("--worktree", "relative"),
        ("--worktree", "/nonexistent"),
        ("--port", "0"),
        ("--ready-nonce", "bad"),
        ("--oversight-origin", "null"),
        ("--oversight-origin", "http://localhost:8710/"),
        ("--memory-limit-bytes", "0"),
    ],
)
async def test_invalid_configuration(tmp_path, extra):
    async with launched(tmp_path, extra=extra, ready=False) as child:
        assert await child.exit() == 70


async def test_standalone_without_oversight_install(tmp_path):
    import sys

    setup = (
        "import importlib.util\nassert importlib.util.find_spec('oversight') is None\n"
        "assert sys.flags.isolated and sys.dont_write_bytecode"
    )
    async with launched(
        tmp_path, interpreter=str(getattr(sys, "_base_executable", sys.executable)), setup=setup
    ) as child:
        assert (await child.request()).status == 200


async def test_adoption_takes_ownership_and_clears_inheritance(tmp_path):
    import os
    from dataclasses import replace

    from oversight import preview_adapter as adapter

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    fd = sock.detach()
    os.set_inheritable(fd, True)
    config = adapter.Config(fd, 1, tmp_path, adapter.MEMORY_BYTES, "a" * 64, ("http://localhost",))
    with socket.socket(fileno=os.dup(fd)) as borrowed:
        config = replace(config, port=borrowed.getsockname()[1])
    adopted = adapter.adopt(config)
    try:
        assert adopted.fileno() == fd and not adopted.get_inheritable()
    finally:
        adopted.close()
    with pytest.raises(OSError):
        os.fstat(fd)


async def test_failed_adoption_closes_regular_file_descriptor(tmp_path, monkeypatch):
    import os

    from oversight import preview_adapter as adapter

    fd = os.open(__file__, os.O_RDONLY)
    config = adapter.Config(fd, 1, tmp_path, adapter.MEMORY_BYTES, "a" * 64, ("http://localhost",))

    def refused(code):
        assert code == 71
        raise RuntimeError("refused")

    monkeypatch.setattr(adapter, "fatal", refused)
    with pytest.raises(RuntimeError, match="refused"):
        adapter.adopt(config)
    with pytest.raises(OSError):
        os.fstat(fd)


async def test_missing_shop_source_is_provenance_refusal(tmp_path):
    root = copy_shop(tmp_path)
    (root / "shop/cart.py").unlink()
    async with launched(tmp_path, root=root, ready=False) as child:
        assert await child.exit() == 72


async def test_actual_late_import_symlink_escape(tmp_path):
    root = copy_shop(tmp_path)
    foreign = tmp_path / "foreign.py"
    foreign.write_text("VALUE=1500\n")
    (root / "shop/late.py").symlink_to(foreign)
    with (root / "shop/cart.py").open("a") as stream:
        stream.write(
            "\ndef late(self):\n    from . import late\n    return late.VALUE\nCart.total=late\n"
        )
    async with launched(tmp_path, root=root) as child:
        with pytest.raises(ValueError):
            await child.request()
        assert await child.exit() == 72
