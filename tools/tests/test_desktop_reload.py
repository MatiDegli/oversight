"""Keep native restart disclosures testable without displaying Windows dialogs."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_reload_confirmation_and_runbook_disclose_reopen_consequences() -> None:
    dialogs = (ROOT / "desktop/shell/src/dialogs.rs").read_text(encoding="utf-8")
    reload = dialogs.split("pub fn confirm_reload(", 1)[1].split("pub fn message(", 1)[0]
    # Inspect the actual TaskDialog content argument, not an unused warning constant.
    arguments = re.search(r'task\(\s*window,\s*"[^"]*",\s*"([^"]*)",', reload)
    assert arguments is not None
    content = arguments[1]
    readme = (ROOT / "desktop/README.md").read_text(encoding="utf-8")
    instructions = readme.split("**File > Reload settings**", 1)[1].split("## Open", 1)[0]
    for text in (content, " ".join(instructions.split())):
        assert "Pending changes are discarded" in text
        assert "approved changes" in text
        assert "restart is refused" in text
        assert "even if you already exported them" in text
        assert "folder holding them is named" in text


def test_generic_startup_remedy_mentions_saved_settings() -> None:
    refusal = (ROOT / "desktop/core/src/refusal.rs").read_text(encoding="utf-8")
    remedy = refusal.split("Self::Other =>", 1)[1]
    assert "settings saved with File > Edit settings…" in remedy


def test_switch_timeout_retry_applies_to_reload_and_open() -> None:
    session = (ROOT / "desktop/shell/src/session.rs").read_text(encoding="utf-8")
    timeout = session.split("Action::PageTimeout =>", 1)[1].split("ui.enable(true)", 1)[0]
    assert "The current session is still running. Try again." in timeout
