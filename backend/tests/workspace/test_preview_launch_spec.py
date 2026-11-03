"""Closed reviewed launch declarations never grant general command execution."""

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from oversight.workspace import PreviewLaunchSpec, load_preview_app
from oversight.workspace.preview_launch_spec import INVALID, OVERRIDE_REFUSAL, UNSUPPORTED

ROOT = Path(__file__).resolve().parents[3]


def prepared_app(tmp_path):
    root = tmp_path / "s"
    (root / "frontend").mkdir(parents=True)
    for name in ("uv.lock", "frontend/pnpm-lock.yaml", "frontend/package.json"):
        (root / name).write_text("prepared dependency fixture")
    payload = json.loads((ROOT / "oversight.preview.json").read_text())
    declaration = root / "oversight.preview.json"
    declaration.write_text(json.dumps(payload))
    resources = {}
    for name in ("python", "node", "tsc", "vite"):
        path = tmp_path / name
        path.write_text("prepared " + name)
        path.chmod(0o700)
        resources[name] = path
    return root, resources, payload


def test_preview_declaration_selection_and_refusals(tmp_path, monkeypatch):
    assert load_preview_app(tmp_path, {}) is None
    root, resources, payload = prepared_app(tmp_path)
    spec = load_preview_app(root, {}, **resources)
    assert isinstance(spec, PreviewLaunchSpec)
    assert spec.provenance == "source declaration" and spec.mode == "static-api"
    assert spec.build[0].argv == (str(resources["node"]), str(resources["tsc"]), "--noEmit")
    assert spec.start.cwd == root
    assert len(spec.executables) == 4 and len(spec.dependencies) == 3
    assert all(len(p.sha256) == 64 for p in (*spec.executables, *spec.dependencies))
    with pytest.raises(FrozenInstanceError):
        spec.mode = "other"  # pyright: ignore[reportAttributeAccessIssue]
    override = tmp_path / "override.json"
    override.write_text(json.dumps(payload))
    (root / "oversight.preview.json").write_text("invalid original")
    chosen = load_preview_app(root, {"OVERSIGHT_PREVIEW_APP": str(override)}, **resources)
    assert chosen is not None
    assert chosen.provenance == "person override"
    monkeypatch.setenv("OVERSIGHT_PREVIEW_APP", "changed environment")
    assert chosen.start.argv == spec.start.argv
    chosen.verify()
    override.write_text("changed after freeze")
    assert chosen.start.argv == spec.start.argv
    with pytest.raises(ValueError, match=INVALID):
        chosen.verify()
    with pytest.raises(ValueError, match=INVALID):
        replace(spec, mode="forged").verify()
    for value in ("", "relative.json", "python -m oversight", str(tmp_path / "absent")):
        with pytest.raises(ValueError, match=OVERRIDE_REFUSAL):
            load_preview_app(root, {"OVERSIGHT_PREVIEW_APP": value}, **resources)
    for data, message in (
        (b"\xff", OVERRIDE_REFUSAL),
        (b"{}", INVALID),
        (b'{"version":1,"version":1}', INVALID),
    ):
        override.write_bytes(data)
        with pytest.raises(ValueError, match=message):
            load_preview_app(root, {"OVERSIGHT_PREVIEW_APP": str(override)}, **resources)


@pytest.mark.parametrize(
    "mutation",
    [
        "root-extra",
        "version",
        "version-bool",
        "app",
        "mode",
        "build-extra",
        "start-extra",
        "readiness-extra",
        "shell",
        "assignment",
        "traversal",
        "absolute-cwd",
        "placeholder",
        "timeout-zero",
        "timeout-big",
        "timeout-bool",
        "build-empty",
        "readiness",
        "argv-string",
        "missing",
        "null",
        "duplicate-nested",
    ],
)
def test_closed_declaration_shapes(tmp_path, mutation):
    root, resources, payload = prepared_app(tmp_path)
    expected = INVALID
    if mutation == "root-extra":
        payload["env"] = {}
    elif mutation == "version":
        payload["version"] = 2
        expected = UNSUPPORTED
    elif mutation == "version-bool":
        payload["version"] = True
        expected = UNSUPPORTED
    elif mutation in {"app", "mode"}:
        payload[mutation] = "arbitrary"
        expected = UNSUPPORTED
    elif mutation == "build-extra":
        payload["build"][0]["env"] = {}
    elif mutation == "start-extra":
        payload["start"]["shell"] = False
    elif mutation == "readiness-extra":
        payload["readiness"]["extra"] = "/"
    elif mutation == "shell":
        payload["start"]["argv"] = ["sh", "-c", "true"]
    elif mutation == "assignment":
        payload["start"]["argv"][0] = "KEY=secret"
    elif mutation == "traversal":
        payload["start"]["cwd"] = "../outside"
    elif mutation == "absolute-cwd":
        payload["start"]["cwd"] = "/outside"
    elif mutation == "placeholder":
        payload["start"]["argv"][0] = "{unknown}"
    elif mutation == "timeout-zero":
        payload["start"]["timeout_s"] = 0
    elif mutation == "timeout-big":
        payload["start"]["timeout_s"] = 901
    elif mutation == "timeout-bool":
        payload["start"]["timeout_s"] = True
    elif mutation == "build-empty":
        payload["build"] = []
    elif mutation == "readiness":
        payload["readiness"]["public"] = "https://foreign/"
    elif mutation == "argv-string":
        payload["start"]["argv"] = "python"
    elif mutation == "missing":
        del payload["readiness"]
    elif mutation == "null":
        payload["start"] = None
    serialized = json.dumps(payload)
    if mutation == "duplicate-nested":
        serialized = serialized.replace('"timeout_s": 30', '"timeout_s": 30, "timeout_s": 30')
    (root / "oversight.preview.json").write_text(serialized)
    with pytest.raises(ValueError, match=expected):
        load_preview_app(root, {}, **resources)


@pytest.mark.parametrize("kind", ["link", "cycle", "directory", "dependency", "resource"])
def test_unsafe_declarations_and_stale_identities(tmp_path, kind):
    root, resources, _ = prepared_app(tmp_path)
    path = root / "oversight.preview.json"
    if kind in {"dependency", "resource"}:
        spec = load_preview_app(root, {}, **resources)
        assert spec is not None
        target = root / "uv.lock" if kind == "dependency" else resources["node"]
        target.write_text("mutated")
        with pytest.raises(ValueError, match=INVALID):
            spec.verify()
        return
    original = path.read_bytes()
    path.unlink()
    if kind == "directory":
        path.mkdir()
    elif kind == "cycle":
        path.symlink_to(path.name)
    else:
        other = tmp_path / "other.json"
        other.write_bytes(original)
        path.symlink_to(other)
    with pytest.raises(ValueError, match=INVALID):
        load_preview_app(root, {}, **resources)
