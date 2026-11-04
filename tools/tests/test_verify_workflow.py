"""Fail-closed checks of canonical workflow blocks; no YAML dependency in the lock.

Only the reviewed mapping, step-list and literal-script forms are accepted. Parse
each block at its own indentation so a later job cannot satisfy an earlier job's policy.
"""

import asyncio
import os
import re
import sys
from pathlib import Path

import pytest

from oversight.workspace.runner import run_process

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github/workflows"
ACTIONS = {
    "actions/checkout": "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1",
    "astral-sh/setup-uv": "astral-sh/setup-uv@1c37ad07a6a961277cf70c0d37d6f313000f5884 # v10.3.0",
    "pnpm/action-setup": "pnpm/action-setup@ea17c68df8912ef543352723c149a84f56e3d413 # v6.1.0",
    "actions/setup-node": "actions/setup-node@949feb2413d6458794dcd2491c4babbbce0c15c1 # v7.1.0",
}
GITLEAKS = "./.github/actions/setup-gitleaks"
TRACKED = {
    "name": "tracked files",
    "run": '|\nstatus=$(git status --porcelain --untracked-files=no)\ntest -z "$status"',
}

STANDALONE_PNPM = {
    "name": "Use standalone pnpm executable",
    "run": r"""|
python3 - <<'PY'
import os
from pathlib import Path

root = Path(os.environ["PNPM_HOME"]).parent.resolve(strict=True)
executables = set()
for candidate in root.glob("**/@pnpm/exe.linux-x64/pnpm"):
    candidate = candidate.resolve(strict=True)
    if not candidate.is_relative_to(root) or not candidate.is_file():
        continue
    with candidate.open("rb") as stream:
        if stream.read(4) == b"\x7fELF" and os.access(candidate, os.X_OK):
            executables.add(candidate)
if len(executables) != 1:
    raise SystemExit("Expected exactly one executable standalone pnpm ELF in setup destination")
with open(os.environ["GITHUB_PATH"], "a") as path:
    path.write(str(executables.pop().parent) + "\n")
PY""",
}


def mapping(text: str) -> dict[str, str]:
    """Consume every line, rejecting duplicate keys and unsupported YAML forms."""
    text = text.strip("\n") + "\n"
    field = re.compile(
        r"^([A-Za-z_][\w-]*):(?: ([^\n]+))?\n((?:^ {2}[^\n]*\n|^\n)*)",
        re.MULTILINE,
    )
    result: dict[str, str] = {}
    offset = 0
    while offset < len(text):
        # Ignore comments only at this mapping's level; literal bodies stay intact.
        if text[offset] == "#":
            offset = text.index("\n", offset) + 1
            continue
        match = field.match(text, offset)
        assert match is not None, f"Unsupported mapping near {text[offset : offset + 80]!r}"
        key, scalar, children = match.groups()
        assert key not in result, f"Duplicate key: {key}"
        body = "\n".join(line[2:] for line in children.splitlines()).rstrip("\n")
        scalar = scalar or ""
        assert not (scalar and body) or scalar == "|", f"Unsupported block: {key}"
        result[key] = scalar + ("\n" if scalar and body else "") + body
        offset = match.end()
    return result


def steps(text: str) -> list[dict[str, str]]:
    """Read only canonical step items; never search across step boundaries."""
    chunks = re.split(r"(?m)(?=^- )", text)
    assert chunks.pop(0) == "", "Expected a step list"
    result: list[dict[str, str]] = []
    for chunk in chunks:
        lines = chunk.splitlines()
        assert lines[0].startswith("- ")
        assert all(not line or line.startswith("  ") for line in lines[1:])
        result.append(mapping("\n".join(line[2:] for line in lines)))
    assert result, "Expected at least one step"
    return result


def assert_ci_triggers(ci: str) -> None:
    triggers = mapping(mapping(ci)["on"])
    assert "pull_request" not in triggers
    assert triggers == {"push": "branches: [main]"}


def assert_actions(job_steps: list[dict[str, str]]) -> None:
    for step in job_steps:
        if "uses" not in step:
            continue
        action = step["uses"]
        if action.startswith("./"):
            assert action == GITLEAKS
        else:
            ref = action.split(" # ", 1)[0]
            assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), action
            assert action == ACTIONS[ref.split("@", 1)[0]]


def assert_named_copy(actual: dict[str, str], source: dict[str, str]) -> None:
    assert re.fullmatch(r"[A-Za-z][A-Za-z ]+", actual.get("name", ""))
    assert {key: value for key, value in actual.items() if key != "name"} == source


def assert_verify_policy(verify: str, ci: str) -> None:
    workflow = mapping(verify)
    assert set(workflow) == {"name", "on", "permissions", "jobs"}
    assert workflow["name"] == "verify"
    assert mapping(workflow["on"]) == {"workflow_dispatch": ""}
    assert workflow["permissions"] == "{}"
    assert_ci_triggers(ci)
    jobs = mapping(workflow["jobs"])
    assert set(jobs) == {"check", "secrets"}
    ci_jobs = mapping(mapping(ci)["jobs"])
    parsed_steps: dict[str, list[dict[str, str]]] = {}
    for job_id, timeout in (("check", "20"), ("secrets", "10")):
        job = mapping(jobs[job_id])
        assert set(job) == {"name", "runs-on", "timeout-minutes", "permissions", "steps"} | (
            {"env"} if job_id == "check" else set()
        )
        assert job["name"] == job_id
        assert job["runs-on"] == ("ubuntu-24.04" if job_id == "check" else "ubuntu-latest")
        assert job["timeout-minutes"] == timeout
        assert mapping(job["permissions"]) == {"contents": "read"}
        job_steps = steps(job["steps"])
        assert_actions(job_steps)
        assert job_steps[-1] == TRACKED
        assert mapping(job_steps[0]["with"])["persist-credentials"] == "false"
        parsed_steps[job_id] = job_steps

    for job in (mapping(jobs["check"]), mapping(ci_jobs["check"])):
        assert mapping(job["env"]) == {"UV_PYTHON_PREFERENCE": "only-managed"}

    check = parsed_steps["check"]
    ci_check = steps(mapping(ci_jobs["check"])["steps"])
    assert len(check) == 12
    assert len(ci_check) == 11
    assert check[3] == ci_check[3] == STANDALONE_PNPM
    check, ci_check = check[:3] + check[4:], ci_check[:3] + ci_check[4:]
    assert len(check) == 11
    assert len(ci_check) == 10
    for actual, source in zip(check[:6], ci_check[:6], strict=True):
        assert_named_copy(actual, source)
    assert [step.get("uses") for step in check[:6]] == [*ACTIONS.values(), None, GITLEAKS]
    assert mapping(check[2]["with"]) == {
        "package_json_file": "frontend/package.json",
        "standalone": "true",
    }
    assert mapping(check[3]["with"]) == {"node-version": "24"}
    assert check[4]["run"] == "make install"
    assert check[6:10] == ci_check[6:10]
    assert check[6] == {
        "name": "Allow unprivileged user namespaces",
        "run": "sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0",
    }
    assert check[7:9] == [{"run": "make check-ci"}, {"run": "make smoke"}]
    assert check[9]["name"] == "Report network isolation probe"

    secrets = parsed_steps["secrets"]
    ci_secrets = steps(mapping(ci_jobs["secrets"])["steps"])
    assert len(secrets) == 4
    for actual, source in zip(secrets[:2], ci_secrets[:2], strict=True):
        assert_named_copy(actual, source)
    assert secrets[0]["uses"] == ACTIONS["actions/checkout"]
    assert mapping(secrets[0]["with"]) == {"fetch-depth": "0", "persist-credentials": "false"}
    assert secrets[1]["uses"] == GITLEAKS
    assert secrets[2] == {
        "name": "Scan HEAD history",
        "run": 'gitleaks git --config .gitleaks.toml --redact --verbose --log-opts="HEAD" .',
    }


def test_verify_has_only_the_manual_backend_jobs_and_required_gates() -> None:
    assert_verify_policy((WORKFLOWS / "verify.yml").read_text(), (WORKFLOWS / "ci.yml").read_text())


@pytest.mark.parametrize(
    "layout", ["flat", "nested", "missing", "script", "mode", "escape", "many"]
)
def test_standalone_pnpm_selection_requires_a_unique_executable_elf(tmp_path, layout):
    root = tmp_path / "setup/node_modules"
    home = root / ".bin"
    home.mkdir(parents=True)
    (home / "pnpm").write_text("#!/bin/sh\nexec node ../pnpm/bin/pnpm.cjs\n")
    target = root / "@pnpm/exe.linux-x64/pnpm"
    if layout == "nested":
        target = root / ".pnpm/exe/node_modules/@pnpm/exe.linux-x64/pnpm"
    if layout != "missing":
        target.parent.mkdir(parents=True)
        target.write_bytes(b"#!/bin/sh\n" if layout == "script" else b"\x7fELFfixture")
        target.chmod(0o644 if layout == "mode" else 0o755)
    if layout == "escape":
        outside = tmp_path / "outside"
        target.rename(outside)
        target.symlink_to(outside)
    if layout == "many":
        other = root / ".pnpm/other/node_modules/@pnpm/exe.linux-x64/pnpm"
        other.parent.mkdir(parents=True)
        other.write_bytes(b"\x7fELFother")
        other.chmod(0o755)
    output = tmp_path / "github-path"
    output.write_text("existing\n")
    script = STANDALONE_PNPM["run"].removeprefix("|\npython3 - <<'PY'\n").removesuffix("PY")
    result = asyncio.run(
        run_process(
            [sys.executable, "-I", "-c", script],
            cwd=tmp_path,
            timeout_s=20,
            env={"PNPM_HOME": str(home), "GITHUB_PATH": str(output)},
        )
    )
    assert not result.timed_out
    # Windows does not expose POSIX execute bits; this selector runs on Linux check jobs.
    if layout in {"flat", "nested"} or (layout == "mode" and os.name == "nt"):
        assert result.returncode == 0, result.stderr
        assert output.read_text() == f"existing\n{target.parent}\n"
    else:
        assert result.returncode != 0
        assert "Expected exactly one executable standalone pnpm ELF" in result.stderr
        assert output.read_text() == "existing\n"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("on:\n  workflow_dispatch:", "on: workflow_dispatch"),
        ("  workflow_dispatch:", "  workflow_dispatch:\n    inputs:\n      task: {}"),
        ("  workflow_dispatch:", "  workflow_dispatch:\n  pull_request:"),
        ("  workflow_dispatch:", "  workflow_dispatch:\n  workflow_dispatch:"),
        ("permissions: {}", "permissions: write-all"),
        ("permissions: {}", "permissions:\n  contents: read"),
        ("      contents: read", "      contents: read\n      id-token: write"),
        ("      contents: read", "      contents: write"),
        ("jobs:", "concurrency: verify\n\njobs:"),
        ("jobs:", "jobs:\n  browser:\n    runs-on: ubuntu-latest"),
        ("    name: check", "    name: make check"),
        ("    name: secrets", "    name: gitleaks"),
        ("    runs-on: ubuntu-latest", "    runs-on: windows-latest"),
        ("    timeout-minutes: 20", "    timeout-minutes: 21"),
        ("    env:\n      UV_PYTHON_PREFERENCE: only-managed\n", ""),
        ("UV_PYTHON_PREFERENCE: only-managed", "UV_PYTHON_PREFERENCE: prefer-managed"),
        ("    timeout-minutes: 10", "    timeout-minutes: 11"),
        ("persist-credentials: false", "persist-credentials: true"),
        ("fetch-depth: 0", "fetch-depth: 1"),
        ("package_json_file: frontend/package.json", "package_json_file: package.json"),
        ("          standalone: true\n", ""),
        ("standalone: true", "standalone: false"),
        ("node-version: 24", "node-version: 22"),
        ("      - name: Set up uv\n", "      - name: Set up uv\n        if: false\n"),
        (
            "      - name: Install dependencies\n",
            "      - name: Install dependencies\n        env: {}\n",
        ),
        ("make install", "uv sync --frozen"),
        ("./.github/actions/setup-gitleaks", "some-owner/setup-gitleaks@main"),
        ("make check-ci", "make check-ci || true"),
        ("make smoke", "make smoke\n        continue-on-error: true"),
        ('{"confirmed", "unavailable"}', '{"confirmed", "unavailable", "failed"}'),
        (' --log-opts="HEAD"', ""),
        ("name: Scan HEAD history", "name: Scan history"),
        ("      - name: tracked files\n", "      - name: Clean tracked files\n"),
        ("git status --porcelain --untracked-files=no", "git status --porcelain"),
        ('test -z "$status"', 'test -n "$status"'),
        ('test -z "$status"', 'test -z "$status" || true'),
        ("status=$(git status", "git restore .\n          status=$(git status"),
        ('test -z "$status"', 'test -z "$status"\n      - run: echo later'),
    ],
)
def test_verify_rejects_policy_regressions(old: str, new: str) -> None:
    verify = (WORKFLOWS / "verify.yml").read_text()
    assert old in verify
    with pytest.raises(AssertionError):
        assert_verify_policy(verify.replace(old, new, 1), (WORKFLOWS / "ci.yml").read_text())


@pytest.mark.parametrize("action", ACTIONS.values())
@pytest.mark.parametrize("ref", ["main", "a" * 40])
def test_verify_rejects_unpinned_or_drifted_actions(action: str, ref: str) -> None:
    verify = (WORKFLOWS / "verify.yml").read_text()
    changed = re.sub(r"@[0-9a-f]{40}", "@" + ref, action)
    with pytest.raises(AssertionError):
        assert_verify_policy(verify.replace(action, changed), (WORKFLOWS / "ci.yml").read_text())


@pytest.mark.parametrize("job", ["check", "secrets"])
def test_tracked_files_is_required_in_each_job(job: str) -> None:
    verify = (WORKFLOWS / "verify.yml").read_text()
    start = verify.index(f"  {job}:\n")
    tracked = verify.index("      - name: tracked files\n", start)
    end = verify.find("\n  secrets:", tracked)
    end = end if end != -1 else len(verify)
    with pytest.raises(AssertionError):
        assert_verify_policy(verify[:tracked] + verify[end:], (WORKFLOWS / "ci.yml").read_text())


@pytest.mark.parametrize("filename", ["ci.yml", "verify.yml"])
@pytest.mark.parametrize("preference", ["prefer-managed", "only-system"])
def test_check_jobs_require_managed_python(filename: str, preference: str) -> None:
    workflows = {name: (WORKFLOWS / name).read_text() for name in ("ci.yml", "verify.yml")}
    workflows[filename] = workflows[filename].replace(
        "UV_PYTHON_PREFERENCE: only-managed", f"UV_PYTHON_PREFERENCE: {preference}", 1
    )
    with pytest.raises(AssertionError):
        assert_verify_policy(workflows["verify.yml"], workflows["ci.yml"])


def test_ci_rejects_a_restored_pull_request_trigger() -> None:
    ci = (WORKFLOWS / "ci.yml").read_text()
    with pytest.raises(AssertionError):
        assert_ci_triggers(ci.replace("on:\n", "on:\n  pull_request:\n", 1))


@pytest.mark.parametrize(
    "filename,job_id",
    [
        ("ci.yml", "browser"),
        ("verify-frontend.yml", "browser-1"),
        ("verify-frontend.yml", "browser-2"),
    ],
)
def test_browser_jobs_use_supported_host(filename: str, job_id: str) -> None:
    jobs = mapping(mapping((WORKFLOWS / filename).read_text())["jobs"])
    assert mapping(jobs[job_id])["runs-on"] == "ubuntu-24.04"


WINDOWS_PATHS = (
    "desktop/**, backend/**, contracts/**, tools/**, frontend/**, demo-repo/**, fixtures/**, "
    "docs/**, "
    "README.md, ARCHITECTURE.md, DECISIONS.md, .github/**, .gitattributes, .python-version, "
    "pyproject.toml, uv.lock, conftest.py, Makefile"
)
WINDOWS_TOOLS = (
    "tools/demo_check_nt.py tools/demo_check.py tools/smoke.py "
    "tools/tests/test_demo_check_nt.py tools/tests/test_demo_check.py tools/tests/test_smoke.py "
    "tools/tests/test_pages_workflow.py tools/tests/test_verify_workflow.py"
)
WINDOWS_BACKEND = (
    "__main__.py api/static.py app.py events/store.py workspace/__init__.py "
    "workspace/_platform.py workspace/capability.py workspace/core.py workspace/files.py "
    "workspace/integrity.py workspace/isolation.py workspace/metadata_io.py workspace/runner.py "
    "workspace/source.py workspace/test_tree.py workspace/tree.py workspace/snapshot.py "
    "workspace/suite_files.py workspace/suite_guard.py agents/tools.py"
)
UPLOAD = "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2"
WINDOWS_NATIVE_IF = "${{ !cancelled() && steps.frontend_build.outcome == 'success' }}"


def assert_windows_policy(text: str) -> None:
    workflow = mapping(text)
    assert set(workflow) == {"name", "on", "permissions", "jobs"}
    assert workflow["name"] == "windows advisory" and workflow["permissions"] == "{}"
    assert mapping(workflow["on"]) == {
        "pull_request": f"paths: [{WINDOWS_PATHS}]",
        "push": f"branches: [main]\npaths: [{WINDOWS_PATHS}]",
        "workflow_dispatch": "",
    }
    assert "secrets" not in text and "continue-on-error" not in text
    assert mapping(workflow["jobs"]).keys() == {"windows"}
    job = mapping(mapping(workflow["jobs"])["windows"])
    assert set(job) == {
        "name",
        "runs-on",
        "timeout-minutes",
        "permissions",
        "defaults",
        "env",
        "steps",
    }
    assert job["name"] == "windows advisory" and job["runs-on"] == "windows-latest"
    assert job["timeout-minutes"] == "75"
    assert mapping(job["permissions"]) == {"contents": "read"}
    assert mapping(mapping(job["defaults"])["run"]) == {"shell": "pwsh"}
    assert mapping(job["env"]) == {
        "META_API_KEY": '""',
        "BASETEN_API_KEY": '""',
        "OVERSIGHT_CROSS_REVIEW": '"false"',
        "OVERSIGHT_WORKSPACE_SOURCE": '""',
        "OVERSIGHT_AGENT_MODEL": "fake",
        "OVERSIGHT_AGENT_MODEL_B": "fake",
        "OVERSIGHT_FAKE_SCRIPT": "fixtures/fake-model/demo-l2-coupon.json",
        "OVERSIGHT_FAKE_SCRIPT_B": "fixtures/fake-model/demo-l2-restock.json",
        "OVERSIGHT_NT_EVIDENCE_DIR": "${{ github.workspace }}/nt-evidence",
    }
    assert_windows_steps(steps(job["steps"]))
    assert (
        "tools/demo_check_nt.py --hard-kill-check" not in text
    )  # driver qualification remains staged
    assert "OVERSIGHT_NT_FULL_DEMO" not in text
    assert_ci_triggers((WORKFLOWS / "ci.yml").read_text())


def assert_windows_steps(items: list[dict[str, str]]) -> None:
    ci = mapping(mapping((WORKFLOWS / "ci.yml").read_text())["jobs"])
    setup = steps(mapping(ci["check"])["steps"])
    assert setup[3] == STANDALONE_PNPM
    setup = (setup[:3] + setup[4:])[:4]
    assert mapping(setup[2]["with"]) == {
        "package_json_file": "frontend/package.json",
        "standalone": "true",
    }
    # Standalone is required only by the Linux check job's real-boundary fixture.
    setup[2] = setup[2] | {"with": "package_json_file: frontend/package.json"}
    assert items[:4] == setup
    assert items[-1] == {
        "name": "Upload Windows evidence",
        "if": "always()",
        "uses": UPLOAD,
        "with": "name: windows-advisory-evidence\npath: nt-evidence/\n"
        "if-no-files-found: error\nretention-days: 7",
    }
    assert UPLOAD in (WORKFLOWS / "ci.yml").read_text()
    assert len(items) == 23
    assert_windows_desktop_checks(items[18:21])
    items = items[:18] + items[21:]
    assert items[7:9] == [
        {"uses": GITLEAKS},
        {
            "name": "Gitleaks version",
            "run": "gitleaks version | Tee-Object -Append nt-evidence/versions.log",
        },
    ]
    items = items[:7] + items[9:]
    for index, step in enumerate(items[4:-1], 4):
        permitted = {"name", "run"}
        if index == 7:
            permitted.add("id")
        if 8 <= index <= 15:
            permitted.add("if")
            assert step.get("if") == WINDOWS_NATIVE_IF
        if index == 13:
            permitted.add("timeout-minutes")
            assert step["timeout-minutes"] == "3"
        if index == 16:
            permitted.add("if")
            assert step["if"] == "always()"
        assert set(step) <= permitted
    assert items[5:7] == [
        {"run": command}
        for command in (
            "uv sync --frozen",
            "pnpm -C frontend install --frozen-lockfile",
        )
    ]
    assert items[7] == {"id": "frontend_build", "run": "pnpm -C frontend build"}
    files = WINDOWS_TOOLS.split() + ["backend/src/oversight/" + f for f in WINDOWS_BACKEND.split()]
    files += [
        "backend/tests/workspace/test_source_bytes.py",
        "backend/tests/workspace/test_source_bytes_windows.py",
        "backend/tests/agents/test_tools_nt_bytes.py",
        "backend/tests/api/test_files.py",
    ]
    assert items[8] == {
        "name": "Windows target typing for landed sites",
        "if": WINDOWS_NATIVE_IF,
        "run": "uv run --frozen pyright --pythonplatform Windows " + " ".join(files),
    }
    assert all((WORKFLOWS.parents[1] / path).is_file() for path in files)
    assert_windows_runtime_checks(items)


def assert_windows_desktop_checks(items: list[dict[str, str]]) -> None:
    commands = [
        (
            "Desktop Rust toolchain",
            "rustup toolchain install 1.98.0 --profile minimal --component rustfmt\n"
            "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"
            "rustup default 1.98.0\n"
            "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }",
        ),
        (
            "Seed desktop Cargo store",
            "cargo fetch --locked --manifest-path desktop/Cargo.toml\n"
            "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }",
        ),
        (
            "Portable desktop checks",
            "$env:CARGO_NET_OFFLINE = 'true'\n"
            "$timer = [System.Diagnostics.Stopwatch]::StartNew()\n"
            "cargo --offline --locked fmt --manifest-path desktop/Cargo.toml --all -- --check\n"
            "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"
            "cargo --offline test --locked --manifest-path desktop/Cargo.toml "
            "-p oversight-desktop-core\n"
            "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"
            "$timer.Stop()\n"
            '"Portable desktop checks: $($timer.Elapsed.TotalSeconds) seconds; '
            'executable/size/native shell acceptance deferred." | '
            "Set-Content nt-evidence/desktop.txt",
        ),
    ]
    assert items == [
        {"name": name, "if": WINDOWS_NATIVE_IF, "run": "|\n" + command}
        for name, command in commands
    ]


def assert_windows_runtime_checks(items: list[dict[str, str]]) -> None:
    for index, name, file, log in (
        (9, "W1 native cases", "backend/tests/workspace/test_platform_windows.py", "w1-tests"),
        (10, "W3 injected and native cases", "tools/tests/test_demo_check_nt.py", "w3-tests"),
    ):
        assert items[index] == {
            "name": name,
            "if": WINDOWS_NATIVE_IF,
            "run": "|\nuv run --frozen pytest -o timeout_method=thread "
            + file
            + f" -rs -s 2>&1 | Tee-Object nt-evidence/{log}.log\n"
            + "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }",
        }
    assert items[11] == {
        "name": "W4b injected and native byte cases",
        "if": WINDOWS_NATIVE_IF,
        "run": "|\nuv run --frozen pytest -o timeout_method=thread "
        "backend/tests/workspace/test_source_bytes.py "
        "backend/tests/workspace/test_source_bytes_windows.py "
        "backend/tests/agents/test_tools_nt_bytes.py -n 2 -rs -s 2>&1 | "
        "Tee-Object nt-evidence/w4b-tests.log\n"
        "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }",
    }
    assert items[12] == {
        "name": "W4a2 injected and native reset cases",
        "if": WINDOWS_NATIVE_IF,
        "run": "|\nuv run --frozen pytest -o timeout_method=thread "
        "backend/tests/workspace/test_source_nt_copy.py "
        "backend/tests/workspace/test_source_nt_reset.py "
        "backend/tests/workspace/test_source_nt_reset_windows.py -n 2 -rs -s 2>&1 | "
        "Tee-Object nt-evidence/w4a2-tests.log\n"
        "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }",
    }
    assert items[13]["run"] == (
        "|\nuv run --frozen python tools/smoke.py 2>&1 | Tee-Object nt-evidence/smoke.log\n"
        "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }"
    )
    assert items[14] == {
        "name": "Private own-copy composition and native fixtures",
        "if": WINDOWS_NATIVE_IF,
        "run": "|\nuv run --frozen pytest -o timeout_method=thread "
        "backend/tests/test_source_nt_startup.py backend/tests/test_source_trust_once.py "
        "backend/tests/workspace/test_source_nt_lifecycle.py "
        "backend/tests/workspace/test_source_nt_windows.py -n 2 -rs -s 2>&1 | "
        "Tee-Object nt-evidence/own-copy-tests.log\n"
        "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"
        "'After public enablement, on both Windows machines: "
        "uv run --frozen python tools/demo_check_nt.py --own-fixtures. "
        "No six-run acceptance collected here.' | "
        "Set-Content nt-evidence/own-driver-pending.txt",
    }
    assert items[15] == {
        "name": "Report unavailable Linux isolation",
        "if": WINDOWS_NATIVE_IF,
        "run": "|\nuv run --frozen python -m oversight isolation-probe "
        "> nt-evidence/isolation-probe.json\n"
        "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"
        "$probe = Get-Content -Raw nt-evidence/isolation-probe.json | ConvertFrom-Json\n"
        "if ($probe.status -ne 'unavailable' -or $probe.reason -ne "
        "'network isolation C is Linux-only') { throw 'Unexpected NT isolation verdict' }",
    }
    versions = items[4]["run"]
    assert "[System.Environment]::OSVersion.VersionString" in versions
    assert all(f"{tool} --version" in versions for tool in ("uv", "git", "node", "pnpm"))
    assert "Private own-copy composition is covered by injections." in versions
    assert "Public own paths remain closed." in versions
    assert "Integrated six-run/two-machine qualification remains pending;" in versions
    assert "skips are not acceptance" in versions
    assert "Set-Content nt-evidence/staging.txt" in versions
    assert "platform.python_version(), type(loop).__name__" in items[16]["run"]
    assert "Tee-Object nt-evidence/python-loop.log" in items[16]["run"]


def test_windows_is_separate_frozen_least_privilege_and_explicitly_staged() -> None:
    assert_windows_policy((WORKFLOWS / "windows.yml").read_text())


@pytest.mark.parametrize(
    "old,new",
    [
        ("branches: [main]", "branches: ['*']"),
        ("docs/**, ", ""),
        ("permissions: {}", "permissions: write-all"),
        ("contents: read", "contents: write"),
        ("shell: pwsh", "shell: bash"),
        ("timeout-minutes: 75", "timeout-minutes: 300"),
        ("desktop/**, ", ""),
        ("rustup toolchain install 1.98.0", "rustup toolchain install stable"),
        ("cargo fetch --locked", "cargo fetch"),
        ("cargo --offline --locked fmt", "cargo fmt"),
        ("cargo --offline test --locked", "cargo test --locked"),
        ("$env:CARGO_NET_OFFLINE = 'true'", "$env:CARGO_NET_OFFLINE = 'false'"),
        ("executable/size/native shell acceptance deferred.", "shell accepted."),
        ("persist-credentials: false", "persist-credentials: true"),
        ("uv sync --frozen", "uv sync"),
        ("      - id: frontend_build\n", "      - id: other_build\n"),
        ("      - id: frontend_build\n        run:", "      - run:"),
        ("node-version: 24", "node-version: 22"),
        ("      - uses: ./.github/actions/setup-gitleaks\n", ""),
        ("gitleaks version", "gitleaks --help"),
        ("--pythonplatform Windows", "--pythonplatform Linux"),
        ("-o timeout_method=thread", ""),
        ("-rs -s", ""),
        ("exit $LASTEXITCODE", "Write-Output ignored"),
        ("$probe.status -ne 'unavailable'", "$probe.status -eq 'failed'"),
        ("if: always()", "if: failure()"),
        ("Private own-copy composition is covered by injections.", "ready"),
        ("Public own paths remain closed.", "ready"),
        ("Integrated six-run/two-machine qualification remains pending;", "ready"),
        ("skips are not acceptance", "ready"),
        ("backend/tests/test_source_nt_startup.py ", ""),
        ("backend/tests/test_source_trust_once.py ", ""),
        ("backend/tests/workspace/test_source_nt_lifecycle.py ", ""),
        ("backend/tests/workspace/test_source_nt_windows.py ", ""),
        ("No six-run acceptance collected here.", "ready"),
        ("tools/tests/test_smoke.py", "tools/tests"),
        ("uses: actions/checkout@", "uses: untrusted/checkout@"),
    ],
)
def test_windows_rejects_policy_regressions(old: str, new: str) -> None:
    text = (WORKFLOWS / "windows.yml").read_text()
    assert old in text
    with pytest.raises(AssertionError):
        assert_windows_policy(text.replace(old, new, 1))


@pytest.mark.parametrize("index", range(10, 21))
@pytest.mark.parametrize(
    "condition",
    [
        None,
        "success()",
        "always()",
        "${{ steps.frontend_build.outcome == 'success' }}",
        "${{ !cancelled() && steps.frontend_build.outcome == 'failure' }}",
        "${{ !cancelled() && steps.other_build.outcome == 'success' }}",
    ],
)
def test_windows_rejects_missing_or_changed_native_condition(
    index: int, condition: str | None
) -> None:
    job = mapping(mapping(mapping((WORKFLOWS / "windows.yml").read_text())["jobs"])["windows"])
    items = steps(job["steps"])
    if condition is None:
        del items[index]["if"]
    else:
        items[index]["if"] = condition
    with pytest.raises(AssertionError):
        assert_windows_steps(items)


@pytest.mark.parametrize("index", range(10))
def test_windows_rejects_native_condition_on_setup(index: int) -> None:
    job = mapping(mapping(mapping((WORKFLOWS / "windows.yml").read_text())["jobs"])["windows"])
    items = steps(job["steps"])
    items[index]["if"] = WINDOWS_NATIVE_IF
    with pytest.raises(AssertionError):
        assert_windows_steps(items)
