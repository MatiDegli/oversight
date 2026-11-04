use std::io;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use oversight_desktop_core::diagnostics::{OutputTail, SessionLog, Stage};
use windows_sys::Win32::UI::WindowsAndMessaging::{MB_ICONERROR, MB_OK, MessageBoxW};

use crate::child::wide;

pub struct Diagnostics {
    app: PathBuf,
    data: PathBuf,
    log: Mutex<SessionLog>,
    pub tail: Arc<Mutex<OutputTail>>,
    exit: Mutex<Option<u32>>,
}

impl Diagnostics {
    pub fn open() -> io::Result<Self> {
        let local = std::env::var_os("LOCALAPPDATA")
            .ok_or_else(|| io::Error::other("LOCALAPPDATA is required"))?;
        let data = PathBuf::from(local).join("Oversight");
        // Only used to recognize fixed module suffixes; launch independently validates payload.
        let app = std::env::current_exe()?
            .parent()
            .ok_or_else(|| io::Error::other("missing install"))?
            .join("app");
        Ok(Self {
            log: Mutex::new(SessionLog::open(&data)?),
            tail: Arc::new(Mutex::new(OutputTail::new(app.clone(), data.clone()))),
            app,
            data,
            exit: Mutex::new(None),
        })
    }

    pub fn begin_child(&mut self) {
        // A fresh pipe target prevents late output from a previous child crossing sessions.
        self.tail = Arc::new(Mutex::new(OutputTail::new(
            self.app.clone(),
            self.data.clone(),
        )));
        *self.exit.lock().unwrap() = None;
    }

    pub fn stage(&self, stage: Stage) -> io::Result<()> {
        self.log.lock().unwrap().advance(stage)
    }

    pub fn backend_exit(&self, exit: Option<u32>) {
        *self.exit.lock().unwrap() = exit;
    }

    pub fn failure(&self, error: &io::Error, smoke: bool) {
        let saved = self
            .log
            .lock()
            .unwrap()
            .failure(
                error,
                *self.exit.lock().unwrap(),
                &self.tail.lock().unwrap(),
            )
            .is_ok();
        if !smoke {
            show_failure(saved);
        }
    }
}

pub fn show_failure(saved: bool) {
    let message = if saved {
        "Oversight could not start.\nSee %LOCALAPPDATA%\\Oversight\\.runtime\\desktop-session.log for the failed stage, system code and sanitized backend traceback."
    } else {
        "Oversight could not start or write its diagnostic log.\nCheck that %LOCALAPPDATA%\\Oversight is a writable local directory without links."
    };
    unsafe {
        MessageBoxW(
            std::ptr::null_mut(),
            wide(message.as_ref()).as_ptr(),
            wide("Oversight".as_ref()).as_ptr(),
            MB_OK | MB_ICONERROR,
        );
    }
}
