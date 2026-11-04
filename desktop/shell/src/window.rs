use std::io;
use std::sync::{
    Arc,
    atomic::{AtomicU64, Ordering},
};

use oversight_desktop_core::URL;
use tauri::webview::PageLoadEvent;
use tauri::{AppHandle, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

use crate::dialogs;

pub const SWITCHING: &str = "http://tauri.localhost/switching.html";

pub struct Window {
    pub native: WebviewWindow,
    pub finished: Arc<AtomicU64>,
}

impl Window {
    pub fn new(app: &AppHandle) -> io::Result<Self> {
        let finished = Arc::new(AtomicU64::new(0));
        let signal = finished.clone();
        let native =
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("switching.html".into()))
                .title("Oversight")
                .inner_size(1280.0, 820.0)
                .on_navigation(|url| {
                    url.origin().ascii_serialization() == "http://127.0.0.1:8710"
                        || url.as_str() == SWITCHING
                })
                .on_page_load(move |_, payload| {
                    if payload.event() == PageLoadEvent::Finished
                        && payload.url().as_str() == SWITCHING
                    {
                        signal.fetch_add(1, Ordering::SeqCst);
                    }
                })
                .build()
                .map_err(io::Error::other)?;
        native.show().map_err(io::Error::other)?;
        Ok(Self { native, finished })
    }

    pub fn backend(&self) -> io::Result<()> {
        self.native
            .navigate(URL.parse().unwrap())
            .map_err(io::Error::other)
    }

    pub fn switching(&self) -> io::Result<u64> {
        let previous = self.finished.load(Ordering::SeqCst);
        self.native
            .navigate(SWITCHING.parse().unwrap())
            .map_err(io::Error::other)?;
        Ok(previous)
    }

    pub fn message(&self, instruction: &str, content: &str) -> io::Result<()> {
        let parent = self.native.clone();
        let (instruction, content) = (instruction.to_owned(), content.to_owned());
        self.native
            .run_on_main_thread(move || dialogs::message(&parent, &instruction, &content))
            .map_err(io::Error::other)
    }
}
