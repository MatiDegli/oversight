"""Fail-closed text checks: the frozen Python lock has no YAML parser.

Security-sensitive blocks deliberately use a fixed, reviewable spelling. A formatting
change to those blocks must update the checks; unsupported YAML constructs cannot silently
widen deployment authority. The smoke script also runs against a fake curl, without a CDN.
"""

import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAGES = ROOT / ".github/workflows/pages.yml"
GATE = """\
(github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main') ||
(github.event_name == 'workflow_run' &&
 github.event.workflow_run.conclusion == 'success' &&
 github.event.workflow_run.event == 'push' &&
 github.event.workflow_run.head_branch == 'main' &&
 github.event.workflow_run.head_repository.full_name == github.repository)
"""


def block(text: str, key: str, indent: int = 0) -> str:
    """Read one canonical block, stopping at its next sibling, without parsing YAML."""
    # Explicit indentation avoids treating a later job's policy as this job's policy.
    child_indent = " " * (indent + 1)
    pattern = rf"^{' ' * indent}{re.escape(key)}:([^\n]*)\n((?:^{child_indent}[^\n]*\n|^\n)*)"
    matches = list(re.finditer(pattern, text, re.MULTILINE))
    assert len(matches) == 1, f"Expected one {key} block at indentation {indent}"
    return textwrap.dedent(matches[0][1].strip() + "\n" + textwrap.dedent(matches[0][2])).strip()


def assert_pinned_actions(text: str) -> None:
    uses = re.findall(r"^\s*(?:-\s*)?uses:\s*(\S+)", text, re.MULTILINE)
    assert uses, "No actions found"
    for action in uses:
        if action.startswith("./"):
            assert (ROOT / action).is_dir(), action
        else:
            assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-fA-F]{40}", action), action


def assert_install_matches_ci(text: str) -> None:
    build = block(block(text, "jobs"), "build")
    ci_jobs = block((PAGES.parent / "ci.yml").read_text(), "jobs")
    for job in (build, block(ci_jobs, "check"), block(ci_jobs, "browser")):
        steps = [
            "\n".join(
                line
                for line in step.splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            )
            for step in re.split(r"(?m)(?=^- )", block(job, "steps"))
            if step.strip()
        ]
        commands = re.findall(r"^(?:- |  )run: (.+)$", "\n".join(steps), re.MULTILINE)
        assert commands[0] == "make install"
        assert [command for command in commands if "make install" in command] == ["make install"]
        assert steps.count("- run: make install") == 1
        install = steps.index("- run: make install")
        assert steps[install + 1] == "- uses: ./.github/actions/setup-gitleaks"


def assert_pages_policy(text: str) -> None:
    assert_pinned_actions(text)
    assert re.findall(r"^\s*(?:-\s*)?uses:\s*(\./\S+)", text, re.MULTILINE) == [
        "./.github/actions/setup-gitleaks"
    ]
    assert block(text, "on") == (
        "workflow_run:\n  workflows: [ci]\n  types: [completed]\n"
        "  branches: [main]\nworkflow_dispatch:"
    )
    assert block(text, "permissions") == "{}"
    assert block(text, "concurrency") == "group: pages\ncancel-in-progress: false"
    jobs = block(text, "jobs")
    assert re.findall(r"^(\w+):", jobs, re.MULTILINE) == ["build", "deploy"]
    build, deploy = block(jobs, "build"), block(jobs, "deploy")
    assert block(build, "permissions") == "contents: read"
    assert block(deploy, "permissions") == "pages: write\nid-token: write"
    assert len(re.findall(r"^\s*permissions:", text, re.MULTILINE)) == 3
    assert block(build, "if") == ">-\n" + GATE.strip()
    assert len(re.findall(r"^\s*if:", text, re.MULTILINE)) == 1
    assert "needs: build\n" in deploy
    assert block(deploy, "environment") == (
        "name: github-pages\nurl: ${{ steps.deployment.outputs.page_url }}"
    )
    assert "ref: ${{ github.event.workflow_run.head_sha || github.sha }}\n" in build
    assert "persist-credentials: false\n" in build
    assert re.findall(r"--base\s+(\S+)", text) == ["/oversight/"]
    assert (
        "pnpm -C frontend exec vite build --mode replay --base /oversight/ "
        "--outDir dist/pages --emptyOutDir --configLoader runner\n"
    ) in build
    assert "cp frontend/dist/pages/index.html frontend/dist/pages/404.html\n" in build
    assert "path: frontend/dist/pages" in build
    assert_install_matches_ci(text)
    assert "secrets" not in text


def test_pages_deploys_only_the_selected_main_commit_with_least_privilege() -> None:
    assert_pages_policy(PAGES.read_text())


def test_build_installs_exactly_as_ci_jobs() -> None:
    assert_install_matches_ci(PAGES.read_text())


@pytest.mark.parametrize("workflow", ["ci.yml", "pages.yml"])
def test_every_remote_action_is_pinned(workflow: str) -> None:
    assert_pinned_actions((PAGES.parent / workflow).read_text())


def test_setup_actions_match_ci_and_pages_actions_keep_the_approved_pins() -> None:
    ci = (PAGES.parent / "ci.yml").read_text()
    pages = PAGES.read_text()
    for name in (
        "actions/checkout",
        "astral-sh/setup-uv",
        "pnpm/action-setup",
        "actions/setup-node",
    ):
        assert re.findall(rf"{name}@[0-9a-f]{{40}}", pages) == list(
            dict.fromkeys(re.findall(rf"{name}@[0-9a-f]{{40}}", ci))
        )
    assert "package_json_file: frontend/package.json" in pages
    assert "node-version: 24" in pages
    for action in (
        "actions/configure-pages@45bfe0192ca1faeb007ade9deae92b16b8254a0d",
        "actions/upload-pages-artifact@fc324d3547104276b827a68afc52ff2a11cc49c9",
        "actions/deploy-pages@368f82528645a54fb793d4d04e342629a3f51346",
    ):
        assert action in pages


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("@3d3c42e5aac5ba805825da76410c181273ba90b1", "@main"),
        (
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "./.github/actions/setup-gitleaks",
        ),
        ("permissions: {}", "permissions: write-all"),
        ("contents: read", "contents: write"),
        ("pages: write", "pages: write\n      actions: write"),
        ("workflow_dispatch:", "workflow_dispatch:\n  pull_request:"),
        ("branches: [main]", "branches: ['*']"),
        ("types: [completed]", "types: [requested]"),
        ("conclusion == 'success'", "conclusion != 'cancelled'"),
        ("workflow_run.event == 'push'", "workflow_run.event == 'pull_request'"),
        ("github.ref == 'refs/heads/main'", "true"),
        ("workflow_run.head_branch == 'main'", "true"),
        ("needs: build", "if: always()\n    needs: build"),
        ("head_sha || github.sha", "head_branch || github.ref"),
        ("--base /oversight/", "--base /"),
        ("--mode replay", "--mode production"),
        (
            "      - run: make install\n",
            "      - run: uv sync --frozen\n"
            "      - run: pnpm -C frontend fetch --frozen-lockfile\n"
            "      - run: pnpm -C frontend install --offline --frozen-lockfile\n",
        ),
        ("      - run: make install\n", "      - run: make install\n      - run: make install\n"),
        ("      - run: make install\n", "      - run: echo setup\n      - run: make install\n"),
        (
            "      - uses: ./.github/actions/setup-gitleaks\n",
            "",
        ),
        (
            "      - run: make install\n      - uses: ./.github/actions/setup-gitleaks\n",
            "      - uses: ./.github/actions/setup-gitleaks\n      - run: make install\n",
        ),
        (
            "      - uses: ./.github/actions/setup-gitleaks\n",
            "      - uses: ./.github/actions/setup-gitleaks\n        if: false\n",
        ),
        (
            "      - uses: ./.github/actions/setup-gitleaks\n",
            "      - uses: ./.github/actions/setup-gitleaks\n        continue-on-error: true\n",
        ),
        ("      contents: read", "      contents: read\n      id-token: write"),
    ],
)
def test_policy_rejects_deployment_regressions(old: str, new: str) -> None:
    text = PAGES.read_text()
    assert old in text
    with pytest.raises(AssertionError):
        assert_pages_policy(text.replace(old, new))


def smoke_script(text: str) -> str:
    steps = re.findall(
        r"^      - name: Smoke test the published HTML and a hashed asset\n"
        r"((?:^        [^\n]*\n|^\n)*)",
        text,
        re.MULTILINE,
    )
    assert len(steps) == 1, "Expected one published HTML smoke step"
    run = block(steps[0], "run", indent=8)
    assert run.startswith("|\n"), "Expected a literal smoke script"
    return run.removeprefix("|\n")


def test_smoke_script_stays_anchored_when_later_run_step_is_appended() -> None:
    text = PAGES.read_text()
    later_step = "      - name: A later step\n        run: |\n          echo later\n"
    script = smoke_script(text + later_step)
    assert script == smoke_script(text)
    assert '"$PAGE_URL" -o /tmp/pages.html' in script


@pytest.mark.parametrize("response", ["valid", "wrong-base", "http-error", "empty-asset"])
def test_published_smoke_checks_base_asset_and_http_failures(tmp_path: Path, response: str) -> None:
    script = smoke_script(PAGES.read_text())
    fake = tmp_path / "curl"
    fake.write_text(
        f"#!{sys.executable}\n"
        + textwrap.dedent("""\
        import json
        import os
        import sys
        from pathlib import Path

        args = sys.argv[1:]
        log = Path(os.environ["CURL_LOG"])
        with log.open("a") as out:
            out.write(json.dumps(args) + "\\n")
        response = os.environ["RESPONSE"]
        if response == "http-error":
            sys.exit(22)
        page = "https://example.test/oversight/"
        if page in args:
            base = "/" if response == "wrong-base" else "/oversight/"
            body = f'<script src="{base}assets/index-abc12345.js"></script>'
        else:
            assert page + "assets/index-abc12345.js" in args
            body = "" if response == "empty-asset" else "console.log('replay');"
        Path(args[args.index("-o") + 1]).write_text(body)
        """)
    )
    fake.chmod(0o755)
    # Isolate the workflow's temporary output when pytest runs these cases in parallel.
    script = script.replace("/tmp/pages", str(tmp_path / "pages"))
    log = tmp_path / "curl.jsonl"
    done = subprocess.run(
        ["bash", "-c", script],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "PAGE_URL": "https://example.test/oversight/",
            "CURL_LOG": str(log),
            "RESPONSE": response,
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert (done.returncode == 0) == (response == "valid"), done.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(calls) == (2 if response in {"valid", "empty-asset"} else 1)
    for args in calls:
        assert "--fail" in args
        for flag, value in (("--retry", "4"), ("--retry-max-time", "120"), ("--max-time", "30")):
            assert args[args.index(flag) + 1] == value
