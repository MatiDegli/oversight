"""The maintainer's exact policy supersedes older text without claiming deferred isolation."""

from pathlib import Path

SOURCE_POLICY = (
    "Product agents use disposable worktrees under .runtime/worktrees/; nested agents use "
    ".runtime/previews/<id>/state/worktrees/. Sources are demo-repo, a pinned profile or the "
    "person\u2019s selected repository. Agents never work in development worktrees of the running "
    "Oversight or in the selected original."
)
TEST_POLICY = (
    "run_tests uses a fixed startup test specification. Demo and source profiles use "
    "Oversight\u2019s "
    "prepared demo-venv or an explicitly supplied equivalent; own repositories use their validated "
    "environment. Project imports map to the disposable tested tree. Profiles declare their test "
    "scope; own repositories retain whole-repository discovery. The person owns own-repository "
    "test timeouts; the model never changes the specification or timeout."
)
PREVIEW_POLICY = (
    "Preview launch selection is person override, then the original source\u2019s "
    "validated declaration, "
    "then supported startup detection. The demo shop uses Oversight's trusted adapter. Invalid "
    "explicit inputs refuse without fallback. The person sees commands, reason, limits and "
    "platform trust before execution. The specification stays fixed; the model can edit code it "
    "executes. Preview uses disposable tested inputs, platform-specific ownership and verified "
    "cleanup. On Linux, the verified namespace/allowlisted-root boundary covers only the "
    "reviewed-app preview\u2019s build, startup and descendants. Separate run_tests "
    "and shop-adapter "
    "launches retain network/PID isolation without filesystem isolation; covering them is a later "
    "front. Native Windows runs trusted code as the person without credential isolation."
)
WARNING = (
    "Native Windows: trusted repository code, same-user execution; original credentials and "
    "parent API remain accessible; no no-key/no-paid-call guarantee."
)


def test_preview_documentation_matches_authorized_scope():
    root = Path(__file__).resolve().parents[2]
    instructions = (root / "AGENTS.md").read_text()
    for paragraph in (SOURCE_POLICY, TEST_POLICY, PREVIEW_POLICY):
        assert paragraph in instructions
    decisions = (
        (root / "DECISIONS.md")
        .read_text()
        .split(
            "## 2026-11-03 — Reviewed self-profile, launch declaration and startup disclosure\n", 1
        )[1]
    )
    assert WARNING in decisions
    flattened = " ".join(decisions.split())
    for phrase in (
        "supersedes conflicting older selection",
        "trusted repositories only",
        "No paid-run authorization",
        "Health.preview_startup is approved for the next free minor",
        "assigns no version",
        "leaves Health/contracts untouched",
        "Consumer rendering acceptance remains pending",
        "Actual builds, nested execution, dependency installation, Linux filesystem boundary, "
        "gateway and nesting supervisor are not delivered",
        "namespace-only network/PID isolation without filesystem isolation",
        "Origin-less HTTP writes remain allowed",
        "Linux own-source work starts after this change",
        "alongside boundary/gateway/nesting work",
        "general declarations follow submission",
        "Reviewed-app execution remains unavailable",
        "Nondismissible “Preview startup”",
        "textContent, JSON.parse, strict validation and text nodes only",
    ):
        assert phrase in flattened
    assert "Reviewed self-profile front (2026-11-03)" in (root / "DEVLOG.md").read_text()
