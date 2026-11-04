"""Fail-closed checks of canonical workflow blocks; no YAML dependency in the lock.

Only the reviewed mapping, step-list and literal-script forms are accepted. Parse
each block at its own indentation so a later job cannot satisfy an earlier job's policy.
"""

import re
from pathlib import Path

import pytest

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
        assert set(job) == {"name", "runs-on", "timeout-minutes", "permissions", "steps"}
        assert job["name"] == job_id
        assert job["runs-on"] == ("ubuntu-24.04" if job_id == "check" else "ubuntu-latest")
        assert job["timeout-minutes"] == timeout
        assert mapping(job["permissions"]) == {"contents": "read"}
        job_steps = steps(job["steps"])
        assert_actions(job_steps)
        assert job_steps[-1] == TRACKED
        assert mapping(job_steps[0]["with"])["persist-credentials"] == "false"
        parsed_steps[job_id] = job_steps

    check = parsed_steps["check"]
    ci_check = steps(mapping(ci_jobs["check"])["steps"])
    assert len(check) == 11
    assert len(ci_check) == 10
    for actual, source in zip(check[:6], ci_check[:6], strict=True):
        assert_named_copy(actual, source)
    assert [step.get("uses") for step in check[:6]] == [*ACTIONS.values(), None, GITLEAKS]
    assert mapping(check[2]["with"]) == {"package_json_file": "frontend/package.json"}
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
        ("    timeout-minutes: 10", "    timeout-minutes: 11"),
        ("persist-credentials: false", "persist-credentials: true"),
        ("fetch-depth: 0", "fetch-depth: 1"),
        ("package_json_file: frontend/package.json", "package_json_file: package.json"),
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
        ("verify-frontend.yml", "browser-3"),
        ("verify-frontend.yml", "browser-4"),
    ],
)
def test_browser_jobs_use_supported_host(filename: str, job_id: str) -> None:
    jobs = mapping(mapping((WORKFLOWS / filename).read_text())["jobs"])
    assert mapping(jobs[job_id])["runs-on"] == "ubuntu-24.04"


def test_browser_workflows_keep_four_matching_bounded_shards() -> None:
    ci = mapping(mapping((WORKFLOWS / "ci.yml").read_text())["jobs"])
    browser = mapping(ci["browser"])
    assert browser["name"] == "browser tests (${{ matrix.shard }}/4)"
    assert mapping(browser["strategy"]) == {
        "fail-fast": "false",
        "matrix": "shard: [1, 2, 3, 4]",
    }
    ci_steps = steps(re.sub(r"(?m)^#.*\n", "", browser["steps"]))
    cache = next(
        step for step in ci_steps if step.get("name") == "Cache browser fixture dependencies"
    )
    cache_values = mapping(cache["with"])
    prefix = "browser-vite-${{ runner.os }}-${{ runner.arch }}-${{ matrix.shard }}-of-4-"
    assert cache_values == {
        "path": ".shots/*-vite-cache",
        "key": prefix + "${{ hashFiles('frontend/pnpm-lock.yaml', 'frontend/vite.config.ts', "
        "'frontend/**/*.support.ts') }}",
        "restore-keys": "|\n" + prefix,
    }
    assert {
        "name": "Monaco and graph tests in Chromium",
        "run": "make test-browser BROWSER_SHARD=${{ matrix.shard }}/4",
    } in ci_steps
    upload = next(
        step for step in ci_steps if step.get("name") == "Upload browser failure evidence"
    )
    assert mapping(upload["with"])["name"] == "browser-failure-evidence-${{ matrix.shard }}-of-4"
    verify = mapping(mapping((WORKFLOWS / "verify-frontend.yml").read_text())["jobs"])
    assert {name for name in verify if name.startswith("browser-")} == {
        f"browser-{shard}" for shard in range(1, 5)
    }
    template = verify["browser-2"]
    for shard in range(1, 5):
        name = f"browser-{shard}"
        assert verify[name] == template.replace("browser-2", name).replace("2/4", f"{shard}/4")
        job = mapping(verify[name])
        assert {
            "name": f"Run {name}",
            "run": f"make test-browser BROWSER_SHARD={shard}/4",
        } in steps(job["steps"])
        for bounded_job in (browser, job):
            assert bounded_job["timeout-minutes"] == "25"
            assert mapping(bounded_job["permissions"]) == {"contents": "read"}


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


def assert_no_job_runner_environment(jobs: dict[str, str]) -> None:
    for name, body in jobs.items():
        for value in mapping(mapping(body).get("env", "")).values():
            for expression in re.findall(r"\$\{\{(.*?)\}\}", value, re.DOTALL):
                assert not re.search(r"\brunner\b", expression, re.IGNORECASE), name


def assert_windows_policy(text: str) -> None:
    workflow = mapping(text)
    assert set(workflow) == {"name", "on", "permissions", "jobs"}
    assert workflow["name"] == "windows advisory" and workflow["permissions"] == "{}"
    assert mapping(workflow["on"]) == {
        "pull_request": f"paths: [{WINDOWS_PATHS}]",
        "push": f"branches: [main, 'desktop/**']\npaths: [{WINDOWS_PATHS}]",
        "workflow_dispatch": "",
    }
    assert "secrets" not in text and "continue-on-error" not in text
    assert mapping(workflow["jobs"]).keys() == {"windows", "package"}
    assert_no_job_runner_environment(mapping(workflow["jobs"]))
    assert_package_policy(mapping(mapping(workflow["jobs"])["package"]))
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
    assert items[:4] == steps(mapping(ci["check"])["steps"])[:4]
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


@pytest.mark.parametrize("job", ["windows", "package"])
@pytest.mark.parametrize(
    "expression", ["${{ runner.temp }}", "${{runner['temp']}}", "${{ toJSON(runner) }}"]
)
def test_windows_rejects_runner_in_any_job_environment(job: str, expression: str) -> None:
    jobs = mapping(mapping((WORKFLOWS / "windows.yml").read_text())["jobs"])
    jobs[job] = jobs[job].replace("env:\n", f"env:\n  INVALID: {expression}\n", 1)
    with pytest.raises(AssertionError, match=job):
        assert_no_job_runner_environment(jobs)


@pytest.mark.parametrize("event", ["pull_request", "push"])
def test_windows_trigger_paths_exclude_notice_only_changes(event: str) -> None:
    workflow = mapping((WORKFLOWS / "windows.yml").read_text())
    trigger = mapping(mapping(workflow["on"])[event])
    paths = trigger["paths"].removeprefix("[").removesuffix("]").split(", ")
    assert {"LICENSE", "THIRD_PARTY_NOTICES.md"}.isdisjoint(paths)


@pytest.mark.parametrize(
    "old,new",
    [
        ("branches: [main, 'desktop/**']", "branches: ['*']"),
        ("docs/**, ", ""),
        ("DECISIONS.md, ", "DECISIONS.md, LICENSE, "),
        ("DECISIONS.md, ", "DECISIONS.md, THIRD_PARTY_NOTICES.md, "),
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


PACKAGE_COMMANDS = {
    "Scan checkout with gitleaks": [
        "gitleaks dir --config .gitleaks.toml --redact --no-banner --log-level warn .",
    ],
    "Prepare exact Rust toolchain and Cargo store": [
        "rustup toolchain install 1.98.0 --profile minimal --component rustfmt",
        "rustup default 1.98.0",
        "cargo fetch --locked --manifest-path desktop/Cargo.toml",
    ],
    "Prepare frozen build inputs": [
        "uv sync --frozen --python 3.12.12",
        "pnpm -C frontend install --frozen-lockfile",
        "pnpm -C frontend build",
        "pnpm dlx @tauri-apps/cli@2.12.0 --version",
    ],
    "Offline locked desktop checks and metadata": [
        "$env:CARGO_NET_OFFLINE = 'true'",
        "New-Item -ItemType Directory -Force package-evidence, desktop/packaging/cache | Out-Null",
        "cargo --offline --locked fmt --manifest-path desktop/Cargo.toml --all -- --check",
        "cargo --offline test --locked --manifest-path desktop/Cargo.toml "
        "-p oversight-desktop-core",
        "cargo --offline metadata --locked --manifest-path desktop/Cargo.toml "
        "--filter-platform x86_64-pc-windows-msvc --format-version 1 "
        "> desktop/packaging/cache/cargo-metadata.json",
        "cargo --offline metadata --locked --manifest-path desktop/Cargo.toml --format-version 1 "
        "> desktop/packaging/cache/cargo-all-metadata.json",
    ],
    "Stage verified embedded runtimes and notices": [
        "uv run --frozen python desktop/packaging/stage.py",
        "Copy-Item desktop/packaging/stage/app/payload.json package-evidence/",
        "Copy-Item desktop/packaging/stage/app/THIRD_PARTY_NOTICES.md package-evidence/",
    ],
}


def assert_package_policy(job: dict[str, str]) -> None:
    assert set(job) == {
        "name",
        "runs-on",
        "timeout-minutes",
        "permissions",
        "defaults",
        "env",
        "steps",
    }
    assert job["name"] == "desktop installer" and job["runs-on"] == "windows-latest"
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
        "PYTHONDONTWRITEBYTECODE": '"1"',
    }
    items = steps(job["steps"])
    assert len(items) == 14
    assert items[0] == {
        "name": "Set desktop Cargo home",
        "run": '|\n"CARGO_HOME=$env:RUNNER_TEMP\\oversight-tauri-cargo" '
        "| Out-File -Append -Encoding utf8 $env:GITHUB_ENV",
    }
    items = items[1:]
    assert items[:5] == [
        {"uses": ACTIONS["actions/checkout"], "with": "persist-credentials: false"},
        {"uses": ACTIONS["astral-sh/setup-uv"], "with": 'version: "0.11.21"'},
        {"uses": ACTIONS["pnpm/action-setup"], "with": "package_json_file: frontend/package.json"},
        {"uses": ACTIONS["actions/setup-node"], "with": 'node-version: "24.11.0"'},
        {"uses": GITLEAKS},
    ]
    for item, (name, commands) in zip(items[5:10], PACKAGE_COMMANDS.items(), strict=True):
        assert set(item) == {"name", "run"} and item["name"] == name
        script = item["run"].removeprefix("|\n").splitlines()
        expected = []
        for command in commands:
            expected.append(command)
            if command.startswith(("cargo ", "uv ", "pnpm ", "rustup ", "gitleaks ")):
                expected.append("if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }")
        assert script == expected
    assert items[10] == {
        "name": "Build locked NSIS installer",
        "run": "|\n$env:CARGO_NET_OFFLINE = 'true'\n"
        "$timer = [System.Diagnostics.Stopwatch]::StartNew()\nPush-Location desktop/shell\n"
        "try {\n  pnpm --offline dlx @tauri-apps/cli@2.12.0 build --ci --bundles nsis "
        "-- --offline --locked\n  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"
        "} finally { Pop-Location }\n$timer.Stop()\n"
        '"Installer build seconds: $($timer.Elapsed.TotalSeconds)" '
        "| Set-Content package-evidence/build.txt",
    }
    assert items[11] == {
        "name": "Install smoke twice and preserve data on uninstall",
        "timeout-minutes": "8",
        "run": "./desktop/packaging/installed-smoke.ps1",
    }
    assert items[12] == {
        "name": "Upload desktop installer",
        "uses": UPLOAD,
        "with": "name: oversight-windows-installer\npath: |\n"
        "  desktop/target/release/bundle/nsis/*-setup.exe\n  package-evidence/\n"
        "if-no-files-found: error\nretention-days: 7",
    }


@pytest.mark.parametrize(
    "old,new",
    [
        ("$env:RUNNER_TEMP\\oversight-tauri-cargo", "$env:USERPROFILE\\.cargo"),
        ('version: "0.11.21"', 'version: "latest"'),
        ('node-version: "24.11.0"', 'node-version: "24"'),
        ("@tauri-apps/cli@2.12.0", "@tauri-apps/cli@latest"),
        ("--bundles nsis -- --offline --locked", "--bundles nsis"),
        ("./desktop/packaging/installed-smoke.ps1", "Write-Output skip"),
        ("name: oversight-windows-installer", "name: other"),
    ],
)
def test_package_rejects_weakened_build_or_acceptance(old, new):
    text = (WORKFLOWS / "windows.yml").read_text()
    assert old in text
    with pytest.raises(AssertionError):
        assert_windows_policy(text.replace(old, new))


@pytest.mark.parametrize(
    "regression",
    [
        "missing",
        "late",
        "version-only",
        "zero-exit",
        "ignored-failure",
        "allowed-failure",
        "conditional",
    ],
)
def test_package_rejects_missing_late_or_nonblocking_secret_scan(regression: str) -> None:
    text = (WORKFLOWS / "windows.yml").read_text()
    scan = (
        "      - name: Scan checkout with gitleaks\n"
        "        run: |\n"
        "          gitleaks dir --config .gitleaks.toml --redact --no-banner --log-level warn .\n"
        "          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"
    )
    assert scan in text
    replacements = {
        "missing": "",
        "late": "",
        "version-only": scan.replace(
            "gitleaks dir --config .gitleaks.toml --redact --no-banner --log-level warn .",
            "gitleaks version",
        ),
        "zero-exit": scan.replace("--redact", "--exit-code 0 --redact"),
        "ignored-failure": scan.replace("exit $LASTEXITCODE", "Write-Output ignored"),
        "allowed-failure": scan.replace(
            "        run:", "        continue-on-error: true\n        run:"
        ),
        "conditional": scan.replace("        run:", "        if: false\n        run:"),
    }
    text = text.replace(scan, replacements[regression], 1)
    if regression == "late":
        marker = "      - name: Stage verified embedded runtimes and notices\n"
        text = text.replace(marker, scan + marker, 1)
    with pytest.raises(AssertionError):
        assert_windows_policy(text)
