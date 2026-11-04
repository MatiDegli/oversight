"""Reproducible Windows payload staging. No provider call or development environment is shipped."""

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
import tarfile
import tomllib
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DROP_DIRS = {"tcl", "tkinter", "idlelib", "turtledemo", "test", "tests", "__pycache__"}
DROP_DLLS = {"_tkinter.pyd", "tcl86t.dll", "tk86t.dll", "_ctypes_test.pyd"}


async def run(*args: str, cwd: Path = ROOT, capture: bool = False) -> str:
    process = await asyncio.create_subprocess_exec(  # noqa: TID251 -- build tool
        *args, cwd=cwd, stdout=asyncio.subprocess.PIPE if capture else None
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), 900)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise RuntimeError(f"staging command failed ({process.returncode}): {args[0]}")
    return stdout.decode("utf-8") if stdout else ""


def verify_archive(archive: Path, manifest: dict[str, str]) -> None:
    with archive.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    if digest != manifest["sha256"]:
        raise ValueError("CPython archive SHA-256 mismatch; refusing extraction")


def download_archive(manifest: dict[str, str], archive: Path) -> None:
    if not archive.exists():
        partial = archive.with_suffix(".partial")
        with (
            urllib.request.urlopen(manifest["url"], timeout=60) as response,
            partial.open("wb") as file,
        ):
            shutil.copyfileobj(response, file)
        partial.replace(archive)
    verify_archive(archive, manifest)


def extract_python(archive: Path, destination: Path) -> None:
    """Verify first, then extract only ordinary relative files under the python/ prefix."""
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        for member in members:
            parts = Path(member.name).parts
            if not parts or parts[0] != "python" or ".." in parts or "\\" in member.name:
                raise ValueError("invalid CPython archive path")
            if not (member.isfile() or member.isdir()) or member.issym() or member.islnk():
                raise ValueError("CPython archive links or special files refused")
        source.extractall(destination, members=members, filter="data")
    python = destination / "python"
    for path in sorted(python.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_dir() and path.name in DROP_DIRS:
            shutil.rmtree(path)
        elif path.is_file() and (
            path.suffix == ".pdb" or path.name in DROP_DLLS or path.name.startswith("_test")
        ):
            path.unlink()
    # Neither pip/ensurepip nor build-machine entry-point launchers are runtime dependencies.
    for relative in ("Scripts", "Lib/site-packages", "Lib/ensurepip", "include", "libs"):
        shutil.rmtree(python / relative, ignore_errors=True)
    (python / "Lib/site-packages").mkdir(parents=True)


def demo_requirements(lock: dict[str, Any], pin: str) -> str:
    packages = {package["name"]: package for package in lock["package"]}
    if packages["pytest"]["version"] != pin:
        raise ValueError("demo pytest pin differs from uv.lock")
    pending, selected = ["pytest"], set[str]()
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        package = packages[name]
        if "registry" not in package["source"]:
            raise ValueError("demo dependency is not a locked registry package")
        selected.add(name)
        pending.extend(dependency["name"] for dependency in package.get("dependencies", []))
    lines = []
    for name in sorted(selected):
        package = packages[name]
        hashes = sorted({wheel["hash"] for wheel in package["wheels"]})
        lines.append(f"{name}=={package['version']} " + " ".join(f"--hash={h}" for h in hashes))
    return "\n".join(lines) + "\n"


def copy_inputs(app: Path) -> None:
    for relative in ("demo-repo", "frontend/dist"):
        source = ROOT / relative
        if not source.is_dir():
            raise ValueError(f"missing prepared payload input: {relative}")
        shutil.copytree(
            source, app / relative, ignore=shutil.ignore_patterns("__pycache__", ".git")
        )
    for relative in (
        "fixtures/layouts/demo-repo.json",
        "fixtures/fake-model/demo-l2-coupon.json",
        "fixtures/fake-model/demo-l2-restock.json",
    ):
        target = app / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    (app / "tools").mkdir()
    shutil.copy2(ROOT / "tools/demo-venv-requirements.txt", app / "tools")
    shutil.copy2(HERE / "config.env.example", app)
    shutil.copy2(ROOT / "LICENSE", app)


async def install_dependencies(app: Path, cache: Path) -> tuple[dict[str, Any], str]:
    runtime, demo = app / "python/python.exe", app / "demo-python/python.exe"
    requirements = cache / "runtime-requirements.txt"
    await run(
        "uv",
        "export",
        "--frozen",
        "--offline",
        "--no-dev",
        "--no-emit-workspace",
        "--output-file",
        str(requirements),
    )
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    pin = next(
        line.removeprefix("pytest==")
        for line in (ROOT / "tools/demo-venv-requirements.txt").read_text().splitlines()
        if line.startswith("pytest==")
    )
    demo_file = cache / "demo-requirements.txt"
    demo_file.write_text(demo_requirements(lock, pin))
    for python, req in ((runtime, requirements), (demo, demo_file)):
        await run(
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--target",
            str(python.parent / "Lib/site-packages"),
            "--no-deps",
            "--only-binary=:all:",
            "--require-hashes",
            "-r",
            str(req),
        )
    return lock, pin


async def build_project_wheels(cache: Path, runtime: Path) -> Path:
    builder = HERE / ".build-python"
    await run("uv", "venv", "--python", sys.executable, str(builder))
    build_python = builder / "Scripts/python.exe"
    await run(
        "uv",
        "pip",
        "install",
        "--python",
        str(build_python),
        "--require-hashes",
        "--no-deps",
        "--only-binary=:all:",
        "-r",
        str(HERE / "build-requirements.txt"),
    )
    wheels = cache / "wheels"
    shutil.rmtree(wheels, ignore_errors=True)
    for package in ("contracts", "backend"):
        await run(
            "uv",
            "build",
            "--wheel",
            "--no-build-isolation",
            "--python",
            str(build_python),
            "--out-dir",
            str(wheels),
            str(ROOT / package),
        )
    await run(
        "uv",
        "pip",
        "install",
        "--python",
        str(runtime),
        "--target",
        str(runtime.parent / "Lib/site-packages"),
        "--no-deps",
        *(str(wheel) for wheel in sorted(wheels.glob("*.whl"))),
    )
    # Local wheel provenance records an absolute build-machine URL, not a runtime requirement.
    # Retain the wheel hashes in payload.json instead.
    await asyncio.to_thread(remove_build_urls, runtime.parent / "Lib/site-packages")
    return wheels


def remove_build_urls(site: Path) -> None:
    for direct_url in site.glob("*.dist-info/direct_url.json"):
        direct_url.unlink()


async def validate_payload(
    app: Path, cache: Path, wheels: Path, manifest: dict[str, str], pin: str
) -> None:
    runtime = app / "python/python.exe"
    copy_inputs(app)
    # A second directory proves this is relocatable, unlike a copied venv.
    relocated = cache / "relocation español 日本"
    shutil.rmtree(relocated, ignore_errors=True)
    shutil.copytree(app / "demo-python", relocated)
    await run(
        str(relocated / "python.exe"),
        "-I",
        "-c",
        f"import pytest; assert pytest.__version__ == {pin!r}",
    )
    shutil.rmtree(relocated)
    await run(
        str(runtime), "-I", "-c", "import oversight, oversight_contracts, sqlite3, ssl, ctypes"
    )
    components = json.loads(
        await run(
            str(runtime),
            "-I",
            "-c",
            "import json,ssl,sqlite3,zlib,decimal,pyexpat; print(json.dumps({"
            "'openssl':ssl.OPENSSL_VERSION,'sqlite':sqlite3.sqlite_version,"
            "'zlib':zlib.ZLIB_VERSION,'mpdecimal':decimal.__libmpdec_version__,"
            "'expat':pyexpat.EXPAT_VERSION}))",
            capture=True,
        )
    )
    await asyncio.to_thread(write_manifest, app, wheels, manifest, pin, components)


def write_manifest(
    app: Path, wheels: Path, manifest: dict[str, str], pin: str, components: dict[str, str]
) -> None:
    (app / "payload.json").write_text(
        json.dumps(
            {
                "python": manifest,
                "pytest": pin,
                "components": components,
                "uv_lock_sha256": hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
                "wheels": {
                    p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in wheels.glob("*.whl")
                },
            },
            indent=2,
        )
        + "\n"
    )


async def stage(output: Path, archive: Path | None) -> None:
    if sys.platform != "win32":
        raise ValueError("complete staging requires native Windows interpreter validation")
    manifest = json.loads((HERE / "python-runtime.json").read_text())
    cache = HERE / "cache"
    cache.mkdir(exist_ok=True)
    archive = archive or cache / "python.tar.gz"
    await asyncio.to_thread(download_archive, manifest, archive)
    if await asyncio.to_thread(output.exists):
        if output != HERE / "stage":
            raise ValueError("refusing to remove an arbitrary output directory")
        shutil.rmtree(output)
    app = output / "app"
    app.mkdir(parents=True)
    extract_python(archive, output)
    shutil.move(str(output / "python"), app / "python")
    shutil.copytree(app / "python", app / "demo-python")
    lock, pin = await install_dependencies(app, cache)
    wheels = await build_project_wheels(cache, app / "python/python.exe")
    await validate_payload(app, cache, wheels, manifest, pin)
    # Installed metadata, crate manifests and exact bundled license texts feed one notice file.
    from licenses import write_notices

    write_notices(app, lock)
    print("Windows payload staged and relocated interpreter imports verified")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path)
    options = parser.parse_args()
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    asyncio.run(stage(HERE / "stage", options.archive))
