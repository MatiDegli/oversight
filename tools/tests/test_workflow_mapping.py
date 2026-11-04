"""Regression coverage for workflow comments and the Linux job host policy."""

import pytest
from tools.tests.test_verify_workflow import WORKFLOWS, assert_verify_policy, mapping, steps


@pytest.mark.parametrize(
    "text",
    [
        "# rationale\nname: check\nruns-on: ubuntu-24.04\n",
        "name: check\n# rationale\nruns-on: ubuntu-24.04\n",
        "name: check\nruns-on: ubuntu-24.04\n# rationale\n",
    ],
)
def test_mapping_accepts_standalone_comments(text: str) -> None:
    assert mapping(text) == {"name": "check", "runs-on": "ubuntu-24.04"}


def test_mapping_preserves_nested_comments_and_literal_script() -> None:
    text = (
        "# workflow\njobs:\n  # host rationale\n  check:\n    runs-on: ubuntu-24.04\n"
        "    run: |\n      # script comment\n      echo '# literal'\n"
    )
    jobs = mapping(mapping(text)["jobs"])
    assert mapping(jobs["check"]) == {
        "runs-on": "ubuntu-24.04",
        "run": "|\n# script comment\necho '# literal'",
    }


@pytest.mark.parametrize(
    "text,reason",
    [
        ("permissions: {}\n# rationale\npermissions: write-all\n", "Duplicate key"),
        ("permissions: {}\n# rationale\n!unsupported\n", "Unsupported mapping"),
        ("# rationale\n<<: *defaults\n", "Unsupported mapping"),
    ],
)
def test_comments_do_not_hide_duplicate_keys_or_unsupported_yaml(text: str, reason: str) -> None:
    with pytest.raises(AssertionError, match=reason):
        mapping(text)


@pytest.mark.parametrize("filename", ["ci.yml", "verify.yml", "verify-frontend.yml"])
def test_supported_job_hosts_and_check_namespace_setup(filename: str) -> None:
    jobs = mapping(mapping((WORKFLOWS / filename).read_text())["jobs"])
    for job_id, body in jobs.items():
        job = mapping(body)
        assert job["runs-on"] == (
            "ubuntu-24.04"
            if job_id in {"check", "browser", "browser-1", "browser-2"}
            else "ubuntu-latest"
        )
    check_steps = steps(mapping(jobs["check"])["steps"])
    check_index = check_steps.index({"run": "make check-ci"})
    assert check_index > 0
    assert check_steps[check_index - 1] == {
        "name": "Allow unprivileged user namespaces",
        "run": "sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0",
    }


@pytest.mark.parametrize(
    "old,new",
    [
        ("runs-on: ubuntu-24.04", "runs-on: ubuntu-latest"),
        ("runs-on: ubuntu-24.04", "runs-on: ubuntu-26.04"),
        (
            "kernel.apparmor_restrict_unprivileged_userns=0",
            "kernel.apparmor_restrict_unprivileged_userns=1",
        ),
        (
            "      - name: Allow unprivileged user namespaces\n"
            "        run: sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0\n",
            "",
        ),
        (
            "      - name: Allow unprivileged user namespaces\n",
            "      - name: Allow unprivileged user namespaces\n        if: false\n",
        ),
    ],
)
def test_verify_rejects_unsupported_host_or_namespace_setup(old: str, new: str) -> None:
    verify = (WORKFLOWS / "verify.yml").read_text()
    ci = (WORKFLOWS / "ci.yml").read_text()
    assert_verify_policy(verify, ci)
    assert old in verify
    with pytest.raises(AssertionError):
        assert_verify_policy(verify.replace(old, new, 1), ci)
