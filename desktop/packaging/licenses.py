"""Fail-closed crate reconciliation and one installed-payload notice document."""

import hashlib
import json
import re
import sys
import tomllib
from importlib import metadata
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from tools import notices  # noqa: E402

# Exact LICENSE.txt bytes from the uv.lock wheel; provenance is in licenses/sources.json.
COLORAMA_0_4_6_LICENSE_SHA256 = "cac35c02686e5d04a5a7140bfb3b36e73aed496656e891102e428886d7930318"

GRANTS = {
    "MPL-2.0": set(["cssparser", "cssparser-macros", "dtoa-short", "option-ext", "selectors"]),
    "Zlib": {"foldhash", "zlib-rs"},
    "Unicode-3.0": set(
        [
            "icu_collections",
            "icu_locale_core",
            "icu_normalizer",
            "icu_normalizer_data",
            "icu_properties",
            "icu_properties_data",
            "icu_provider",
            "litemap",
            "potential_utf",
            "tinystr",
            "unicode-ident",
            "writeable",
            "yoke",
            "yoke-derive",
            "zerofrom",
            "zerofrom-derive",
            "zerotrie",
            "zerovec",
            "zerovec-derive",
        ]
    ),
}


def crate_allowed(name: str, version: str, expression: str, active: bool) -> bool:
    if name == "target-lexicon" and active:
        return False  # Its exact grant covers the inactive Linux branch only.
    expression = re.sub(r"\s*/\s*", " OR ", expression)
    if notices.allowed_license(
        expression, name, version=version, ecosystem="Rust", lockfile="desktop/Cargo.lock"
    ):
        return True
    if name == "unicode-ident" and expression == "(MIT OR Apache-2.0) AND Unicode-3.0":
        return True
    return name in GRANTS.get(expression, set())


def crate_inventory(document: dict[str, Any]) -> tuple[list[str], list[str]]:
    active = {node["id"] for node in document["resolve"]["nodes"]}
    lines, texts = [], []
    locked = tomllib.loads((ROOT / "desktop/Cargo.lock").read_text())["package"]
    expected = {(p["name"], p["version"]) for p in locked if "source" in p}
    seen = set[tuple[str, str]]()
    for package in sorted(document["packages"], key=lambda p: (p["name"], p["version"])):
        if package["source"] is None:
            continue
        name, version, expression = package["name"], package["version"], package["license"]
        included = package["id"] in active
        if not crate_allowed(name, version, expression, included):
            raise ValueError(f"unapproved crate: {name} {version}: {expression}")
        seen.add((name, version))
        lines.append(
            f"| {name} | {version} | {expression} | {'Windows' if included else 'not shipped'} |"
        )
        if included:
            folder = Path(package["manifest_path"]).parent
            files = [
                p
                for p in folder.iterdir()
                if p.is_file() and re.match(r"^(licen[cs]e|copying|notice)([._-]|$)", p.name, re.I)
            ]
            if not files and name == "selectors" and expression == "MPL-2.0":
                # The published crate carries the MPL notice in lib.rs, without a LICENSE file.
                header = (folder / "lib.rs").read_text(encoding="utf-8").split("*/", 1)[0] + "*/"
                texts.append(f"### selectors {version} — source license notice\n\n{header}\n")
                files = [HERE / "licenses/mpl-2.0.txt"]
            if not files:
                for item in json.loads((HERE / "licenses/sources.json").read_text()):
                    if (item.get("crate"), item.get("version")) == (name, version):
                        file = HERE / "licenses" / item["file"]
                        if hashlib.sha256(file.read_bytes()).hexdigest() != item["sha256"]:
                            raise ValueError("pinned upstream crate license changed")
                        files = [file]
            if not files:
                raise ValueError(f"missing crate notice text: {name} {version}")
            for file in sorted(files):
                texts.append(
                    f"### {name} {version} — {file.name}\n\n```text\n"
                    + file.read_text(encoding="utf-8").strip()
                    + "\n```\n"
                )
            if expression == "MPL-2.0":
                texts.append(
                    f"Unmodified source: https://crates.io/api/v1/crates/{name}/{version}/download\n"
                )
    if seen != expected:
        raise ValueError("Cargo metadata does not cover every locked external crate")
    return lines, texts


def python_notices(app: Path, lock: dict[str, Any]) -> list[str]:
    versions = {p["name"]: p["version"] for p in lock["package"]}
    local = {p["name"] for p in lock["package"] if "editable" in p["source"]}
    result = []
    for runtime in ("python", "demo-python"):
        for dist in sorted(
            metadata.distributions(path=[str(app / runtime / "Lib/site-packages")]),
            key=lambda d: d.metadata["Name"],
        ):
            name = dist.metadata["Name"].lower().replace("_", "-")
            if dist.version != versions.get(name):
                raise ValueError(f"unlocked installed package: {name} {dist.version}")
            if name in local:
                continue
            expression = notices.python_license(dist.metadata)
            if (name, dist.version, expression) == ("colorama", "0.4.6", "UNKNOWN"):
                for file in dist.files or ():
                    if file.as_posix() == "colorama-0.4.6.dist-info/licenses/LICENSE.txt":
                        digest = hashlib.sha256(Path(str(dist.locate_file(file))).read_bytes())
                        if digest.hexdigest() == COLORAMA_0_4_6_LICENSE_SHA256:
                            expression = "BSD-3-Clause"
            if not notices.allowed_license(expression, name):
                raise ValueError(f"unapproved Python package: {name}: {expression}")
            result.append(f"### {runtime}: {name} {dist.version} — {expression}\n")
            files = [
                file
                for file in dist.files or ()
                if re.match(r"^(licen[cs]e|copying|notice)([._-]|$)", file.name, re.I)
            ]
            if not files:
                raise ValueError(f"missing Python license text: {name}")
            for file in files:
                result.append(
                    f"```text\n{Path(str(dist.locate_file(file))).read_text(encoding='utf-8')}\n```\n"
                )
    return result


def write_notices(app: Path, lock: dict[str, Any]) -> None:
    document = json.loads((HERE / "cache/cargo-metadata.json").read_text(encoding="utf-8-sig"))
    # Filtered Cargo metadata omits inactive packages; the full graph supplies every lock entry.
    complete = json.loads((HERE / "cache/cargo-all-metadata.json").read_text(encoding="utf-8-sig"))
    document["packages"] = complete["packages"]
    rows, texts = crate_inventory(document)
    components = json.loads((app / "payload.json").read_text())["components"]
    lines = [
        "# Oversight desktop third-party notices\n",
        "Crates are unmodified. Build dependencies are included conservatively. "
        "Inactive entries are not distributed; target-lexicon has an exact lock-only grant.\n",
        "| Crate | Version | License | Inclusion |",
        "| --- | --- | --- | --- |",
        *rows,
        "\n## CPython 3.12.12 and incorporated components\n",
        "Both interpreters use the pinned python-build-standalone 20251014 install_only archive. "
        "Tcl/Tk, tests, pip, ensurepip, headers and build-machine launchers are removed.\n",
        "| Component | Version | License / terms |",
        "| --- | --- | --- |",
        "| CPython and stdlib | 3.12.12 | PSF-2.0; retained historical/incorporated terms below |",
        "| bzip2 | 1.0.8 | bzip2-1.0.6 (outside the named allowlist; disclosed) |",
        "| libffi | 3.4.6 | MIT |",
        f"| OpenSSL | {components['openssl']} | Apache-2.0 |",
        f"| SQLite | {components['sqlite']} | Public domain (outside the named allowlist) |",
        "| liblzma | 5.2.12 | Public domain (outside the named allowlist; disclosed) |",
        f"| zlib | {components['zlib']} | Zlib |",
        f"| libmpdec | {components['mpdecimal']} | BSD-2-Clause |",
        f"| Expat | {components['expat']} | MIT |",
        "| Microsoft vcruntime140 / vcruntime140_1 | runtime 140 | Microsoft Distributable Code; "
        "outside the named allowlist; restrictions reproduced below |",
        "| WebView2 SDK loader | 1.0.3800.47 | BSD-3-Clause (Microsoft) |",
        "\nThe CPython grant does not relicense these components. Microsoft's restrictions apply "
        "to its Distributable Code. The complete shipped CPython license and incorporated "
        "copyright/permission notices follow.\n",
    ]
    for item in json.loads((HERE / "licenses/sources.json").read_text()):
        file = HERE / "licenses" / item["file"]
        if hashlib.sha256(file.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError(f"changed pinned license text: {file.name}")
        lines.extend(
            [
                f"\n### {file.stem}\n\nSource: {item['url']}\n",
                "```text",
                file.read_text(encoding="utf-8"),
                "```\n",
            ]
        )
    lines.extend(
        [
            "\n## Python packages\n",
            *python_notices(app, lock),
            "\n## Rust license and notice texts\n",
            *texts,
        ]
    )
    frontend = app / "frontend/dist/THIRD_PARTY_NOTICES.md"
    if not frontend.is_file():
        raise ValueError("missing frozen frontend notices")
    lines.extend(
        [
            "\n## Frontend dependencies\n",
            frontend.read_text(encoding="utf-8"),
            "\n## NSIS distribution\n",
            "nsis-tauri-utils 0.5.3 is MIT; its complete license is retained above.\n",
            "NSIS 3.11, zlib compressor only. No LZMA or bzip2 installer compressor is used. "
            "The upstream notice distinguishes installer code from compiler tools.\n",
        ]
    )
    (app / "THIRD_PARTY_NOTICES.md").write_text("\n".join(lines), encoding="utf-8")
