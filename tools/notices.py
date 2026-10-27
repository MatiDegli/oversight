"""Offline runtime inventory, notices and strict license policy for the frozen installs.

Run with the shared backend/contracts workspace environment (make notices). Development
packages are excluded by traversing Requires-Dist from those two workspace members, with
markers/extras evaluated for the installed platform. pnpm supplies the frontend inventory.
No registry, model API or package installer is called here.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = frozenset({"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC"})
FONTS = frozenset(
    {"@fontsource/atkinson-hyperlegible-mono", "@fontsource/atkinson-hyperlegible-next"}
)
OFL_HEADER = "SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007"


@dataclass
class Package:
    ecosystem: str
    name: str
    version: str
    license: str
    url: str
    parents: set[str] = field(default_factory=set[str])
    texts: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])


def allowed_license(expression: str, name: str = "") -> bool:
    """Parse SPDX AND/OR/parentheses, failing closed on unknown IDs or WITH clauses."""
    tokens = re.findall(r"\(|\)|[^\s()]+", expression)
    index = 0
    allowed = ALLOWED | ({"OFL-1.1"} if name in FONTS else set[str]())

    def atom() -> bool:
        nonlocal index
        if index >= len(tokens):
            raise ValueError("Missing license")
        token = tokens[index]
        index += 1
        if token == "(":
            result = either()
            if index >= len(tokens) or tokens[index] != ")":
                raise ValueError("Unclosed license group")
            index += 1
            return result
        if token in {"AND", "OR", ")"}:
            raise ValueError("Unexpected license operator")
        if index < len(tokens) and tokens[index] == "WITH":
            index += 1
            if index >= len(tokens) or tokens[index] in {"AND", "OR", "(", ")", "WITH"}:
                raise ValueError("Missing license exception")
            index += 1
            return False  # No exception is part of the approved policy.
        return token in allowed

    def both() -> bool:
        nonlocal index
        result = atom()
        while index < len(tokens) and tokens[index] == "AND":
            index += 1
            right = atom()
            result = result and right
        return result

    def either() -> bool:
        nonlocal index
        result = both()
        while index < len(tokens) and tokens[index] == "OR":
            index += 1
            right = both()
            result = result or right
        return result

    try:
        return either() and index == len(tokens)
    except ValueError:
        return False


def pnpm(*args: str) -> Any:
    return json.loads(
        subprocess.run(
            ["pnpm", "-C", str(ROOT / "frontend"), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout
    )


def frontend_packages() -> list[Package]:
    frontend = ROOT / "frontend"
    # pnpm 12 stores its package-manager lock in the first YAML document. The final
    # document is the application lock copied into node_modules by a frozen install.
    locked = (frontend / "pnpm-lock.yaml").read_text().split("---\n")[-1]
    if locked != (frontend / "node_modules/.pnpm/lock.yaml").read_text():
        raise ValueError("Frontend install differs from pnpm-lock.yaml; install the frozen lock")
    listing: dict[str, list[dict[str, Any]]] = pnpm("licenses", "list", "--prod", "--json")
    parents: dict[tuple[str, str], set[str]] = defaultdict(set)

    def walk(node: dict[str, Any], parent: str) -> None:
        for kind in ("dependencies", "optionalDependencies"):
            for name, dep in node.get(kind, {}).items():
                parents[name, dep["version"]].add(parent)
                walk(dep, f"{name}@{dep['version']}")

    for tree in pnpm("list", "--prod", "--depth", "Infinity", "--json"):
        walk(tree, "frontend")
    result: list[Package] = []
    for entries in listing.values():
        for entry in entries:
            for version in entry["versions"]:
                paths = [Path(p) for p in entry["paths"]]
                installed = next(
                    p
                    for p in paths
                    if json.loads((p / "package.json").read_text())["version"] == version
                )
                info = json.loads((installed / "package.json").read_text())
                name = entry["name"]
                url = info.get("homepage") or f"https://www.npmjs.com/package/{name}/v/{version}"
                texts = [
                    (p.name, p.read_text().strip())
                    for p in sorted(installed.iterdir())
                    if p.is_file() and re.match(r"^(licen[cs]e|copying|notice)(\.|$)", p.name, re.I)
                ]
                result.append(
                    Package(
                        "Frontend",
                        name,
                        version,
                        entry.get("license") or "UNKNOWN",
                        url,
                        parents[name, version],
                        texts,
                    )
                )
    direct = json.loads((frontend / "package.json").read_text())["dependencies"]
    versions = {p.name: p.version for p in result}
    if any(versions.get(name) != version for name, version in direct.items()):
        raise ValueError("Missing or stale direct frontend dependency in pnpm license listing")
    return result


def python_license(info: metadata.PackageMetadata) -> str:
    # An ambiguous generic BSD classifier must not be guessed to mean two or three clauses.
    explicit = info.get("License-Expression") or info.get("License")
    if explicit:
        return explicit.strip()
    classifiers = {
        "License :: OSI Approved :: MIT License": "MIT",
        "License :: OSI Approved :: Apache Software License": "Apache-2.0",
        "License :: OSI Approved :: ISC License (ISCL)": "ISC",
    }
    options = sorted({classifiers[c] for c in info.get_all("Classifier", []) if c in classifiers})
    return " OR ".join(options) or "UNKNOWN"


def python_url(info: metadata.PackageMetadata, name: str, version: str) -> str:
    urls = dict(item.split(", ", 1) for item in info.get_all("Project-URL", []) if ", " in item)
    for label in ("Source", "Source code", "Repository", "Homepage", "Home"):
        if label in urls:
            return urls[label]
    return info.get("Home-page") or f"https://pypi.org/project/{name}/{version}/"


def python_packages() -> list[Package]:
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    versions = {p["name"]: p["version"] for p in lock["package"]}
    local = {p["name"] for p in lock["package"] if "editable" in p["source"]}
    found: dict[str, Package] = {}
    visited: set[tuple[str, frozenset[str]]] = set()

    def visit(name: str, parent: str, extras: frozenset[str] = frozenset()) -> None:
        name = canonicalize_name(name)
        dist = metadata.distribution(name)
        if dist.version != versions.get(name):
            raise ValueError(f"{name}@{dist.version} differs from uv.lock")
        if name not in local:
            if name not in found:
                texts = [
                    (Path(str(p)).name, dist.locate_file(p).read_text().strip())
                    for p in sorted(dist.files or [], key=str)
                    if re.match(r"^(licen[cs]e|copying|notice)(\.|$)", Path(str(p)).name, re.I)
                ]
                found[name] = Package(
                    "Python",
                    name,
                    dist.version,
                    python_license(dist.metadata),
                    python_url(dist.metadata, name, dist.version),
                    texts=texts,
                )
            found[name].parents.add(parent)
        if (name, extras) in visited:
            return
        visited.add((name, extras))
        for text in dist.requires or []:
            requirement = Requirement(text)
            if requirement.marker and not any(
                requirement.marker.evaluate({"extra": extra}) for extra in {"", *extras}
            ):
                continue
            child = metadata.distribution(requirement.name)
            if not requirement.specifier.contains(child.version):
                raise ValueError(f"Installed {requirement.name} does not satisfy {text}")
            visit(requirement.name, f"{name}@{dist.version}", frozenset(requirement.extras))

    visit("oversight", "backend")
    visit("oversight-contracts", "contracts")
    return list(found.values())


def font_notice(packages: list[Package]) -> str:
    headers: list[str] = []
    bodies: set[str] = set()
    for package in packages:
        if package.name not in FONTS:
            continue
        license_text = dict(package.texts)["LICENSE"]
        header, body = license_text.split(OFL_HEADER, 1)
        # Retain each font's copyright and reserved-name statements, then the shared text once.
        headers.append(f"### {package.name} {package.version}\n\n{header.strip()}\n")
        bodies.add(OFL_HEADER + body)
    if len(headers) != 2 or len(bodies) != 1:
        raise ValueError("Expected the two Atkinson fonts with identical OFL 1.1 license bodies")
    return "\n".join(headers) + "\n```text\n" + bodies.pop() + "\n```\n"


def render(packages: list[Package]) -> str:
    ordered = sorted(packages, key=lambda p: (p.ecosystem, p.name, p.version))
    lines = [
        "# Third-party notices",
        "",
        "Generated offline by `make notices` from frozen installed runtime dependencies.",
        "Frontend: `pnpm licenses list --prod`; Python: backend/contracts `Requires-Dist`",
        "closure from `importlib.metadata`, with installed-platform markers and extras.",
        "Development-only packages and this repository's workspace members are excluded.",
        "",
        "| Ecosystem | Name | Version | License | Source URL |",
        "| --- | --- | --- | --- | --- |",
    ]
    for p in ordered:
        cells = [p.ecosystem, p.name, p.version, p.license, p.url]
        lines.append(
            "| " + " | ".join(c.replace("|", "\\|").replace("\n", " ") for c in cells) + " |"
        )
    monaco = next(p for p in ordered if p.name == "monaco-editor")
    lines += [
        "",
        "## Monaco's bundled third-party notices",
        "",
        f"monaco-editor {monaco.version} includes its own `ThirdPartyNotices.txt`, incorporated",
        "by reference in addition to its MIT license below:",
        f"https://unpkg.com/monaco-editor@{monaco.version}/ThirdPartyNotices.txt",
        "",
        "The replay build also ships this installed file as `monaco-editor.ThirdPartyNotices.txt`.",
        "",
        "## Atkinson Hyperlegible fonts — SIL OFL 1.1",
        "",
        font_notice(ordered).rstrip(),
        "",
        "## Installed license and notice texts",
        "",
    ]
    for p in ordered:
        if p.name in FONTS:
            continue
        for filename, body in p.texts:
            lines += [f"### {p.name} {p.version} — {filename}", "", "```text", body, "```", ""]
    return "\n".join(lines).rstrip() + "\n"


def policy_errors(packages: list[Package]) -> list[str]:
    return [
        f"{p.name}@{p.version}: {p.license}; required by {', '.join(sorted(p.parents))}"
        for p in sorted(packages, key=lambda p: (p.name, p.version))
        if not allowed_license(p.license, p.name)
    ]


def check(packages: list[Package], output: Path, generated: str) -> list[str]:
    errors = policy_errors(packages)
    if not output.exists() or output.read_bytes() != generated.encode("utf-8"):
        errors.append("THIRD_PARTY_NOTICES.md is stale or missing; run make notices")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        packages = frontend_packages() + python_packages()
        generated = render(packages)
        output = ROOT / "THIRD_PARTY_NOTICES.md"
        if args.check:
            errors = check(packages, output, generated)
        else:
            output.write_bytes(generated.encode("utf-8"))
            errors = []  # Generation records all licenses; only the check enforces policy.
        counts = Counter(p.license for p in packages)
        print(
            f"Runtime dependencies: {len(packages)}; "
            + "; ".join(f"{license}: {count}" for license, count in sorted(counts.items()))
        )
        for error in errors:
            print(error, file=sys.stderr)
        return int(bool(errors))
    except (OSError, ValueError, subprocess.SubprocessError, metadata.PackageNotFoundError) as exc:
        print(f"Notices failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
