"""Every manifest task must be reproducibly broken and have a working reference."""

import pytest
from tools.eval.grade import check_suites
from tools.eval.tasks import BenchError, load_tasks, safe_path


@pytest.mark.anyio
@pytest.mark.parametrize("task", load_tasks(), ids=lambda t: t.id)
async def test_baseline_and_reference(task, tmp_path):
    before = await check_suites(task.files(), task, tmp_path / "baseline")
    assert before.baseline == task.initial_original_suite
    assert before.hidden == "failed", f"{task.id}: starting task is already solved"
    reference = await check_suites(task.files(reference=True), task, tmp_path / "reference")
    assert reference.baseline == reference.hidden == reference.policy == "passed"


@pytest.mark.parametrize(
    "path", ["../secret", "/tmp/file", "a/../b", "./a", "a//b", "a\\b", ".git/x"]
)
def test_paths_are_relative_plain_paths(path):
    with pytest.raises(BenchError):
        safe_path(path)


def test_hash_pin_rejects_drift():
    task = load_tasks()[0].model_copy(update={"baseline_hash": "0" * 64})
    with pytest.raises(BenchError, match="baseline hash"):
        task.files()
