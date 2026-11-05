//! When a console exists, its close and Ctrl+C request the same cleanup as a window close.
use std::io;
use std::sync::{
    Arc, OnceLock,
    atomic::{AtomicBool, Ordering},
};
use std::time::{Duration, Instant};

use windows_sys::Win32::System::Console::*;

static STOP: OnceLock<Arc<AtomicBool>> = OnceLock::new();
static DONE: AtomicBool = AtomicBool::new(false);

unsafe extern "system" fn control(event: u32) -> i32 {
    if !matches!(
        event,
        CTRL_C_EVENT
            | CTRL_BREAK_EVENT
            | CTRL_CLOSE_EVENT
            | CTRL_LOGOFF_EVENT
            | CTRL_SHUTDOWN_EVENT
    ) {
        return 0;
    }
    if let Some(stop) = STOP.get() {
        stop.store(true, Ordering::SeqCst);
        let deadline = Instant::now() + Duration::from_secs(5);
        while !DONE.load(Ordering::SeqCst) && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(10));
        }
    }
    1
}

/// Whether this process has a console. The windowed app has none; a test harness does.
pub fn attached() -> bool {
    unsafe { GetConsoleCP() != 0 }
}

pub fn install(stop: Arc<AtomicBool>) -> io::Result<()> {
    STOP.set(stop)
        .map_err(|_| io::Error::other("console handler already installed"))?;
    // Never allocate one: it would show the console window this program exists to avoid.
    if !attached() {
        return Ok(());
    }
    unsafe {
        if SetConsoleCtrlHandler(Some(control), 1) == 0 {
            return Err(io::Error::last_os_error());
        }
    }
    Ok(())
}

pub fn completed() {
    DONE.store(true, Ordering::SeqCst);
}
