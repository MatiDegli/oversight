//! Native dialogs, called only on the window's UI thread.
use std::mem::{size_of, zeroed};
use std::ptr::{null, null_mut};

use tauri::WebviewWindow;
use windows_sys::Win32::UI::Controls::*;
use windows_sys::Win32::UI::WindowsAndMessaging::IDCANCEL;

use crate::child::wide;

pub const TRUST: &str = "Oversight copies this repository into a disposable workspace and changes your folder only when you apply an export yourself. Its tests and plugins run as your Windows user with network access and can write anywhere. Code the agents read is sent to the configured model providers. The repository needs a prepared `.venv` (Python 3.10+, pytest 8.2+, pluggy 1.5+); Oversight installs nothing. The current session stops. Opening a repository again discards its earlier pending changes; if that session approved changes you never exported, the reopen is refused and the folder holding them is named.";

const OPEN: i32 = 100;

pub fn confirm(window: &WebviewWindow) -> bool {
    task(window, "Open this repository in Oversight?", TRUST, true)
}

pub fn message(window: &WebviewWindow, instruction: &str, content: &str) {
    task(window, instruction, content, false);
}

fn task(window: &WebviewWindow, instruction: &str, content: &str, confirm: bool) -> bool {
    let Ok(parent) = window.hwnd() else {
        return false;
    };
    let title = wide("Oversight".as_ref());
    let instruction = wide(instruction.as_ref());
    let content = wide(content.as_ref());
    let open = wide("Open".as_ref());
    let button = TASKDIALOG_BUTTON {
        nButtonID: OPEN,
        pszButtonText: open.as_ptr(),
    };
    let mut config: TASKDIALOGCONFIG = unsafe { zeroed() };
    config.cbSize = size_of::<TASKDIALOGCONFIG>() as u32;
    config.hwndParent = parent.0 as _;
    config.dwFlags = TDF_ALLOW_DIALOG_CANCELLATION | TDF_POSITION_RELATIVE_TO_WINDOW;
    config.pszWindowTitle = title.as_ptr();
    config.pszMainInstruction = instruction.as_ptr();
    config.pszContent = content.as_ptr();
    config.dwCommonButtons = if confirm {
        TDCBF_CANCEL_BUTTON
    } else {
        TDCBF_OK_BUTTON
    };
    config.nDefaultButton = if confirm { IDCANCEL } else { 1 };
    config.cButtons = u32::from(confirm);
    config.pButtons = if confirm { &button } else { null() };
    let mut pressed = 0;
    let result = unsafe { TaskDialogIndirect(&config, &mut pressed, null_mut(), null_mut()) };
    result >= 0 && confirm && pressed == OPEN
}
