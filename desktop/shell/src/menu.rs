use std::io;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, mpsc::Sender};

use oversight_desktop_core::refusal::Refusal;
use oversight_desktop_core::source::SelectedSource;
use tauri::menu::{Menu, MenuItem, Submenu};
use tauri::{AppHandle, WebviewWindow};

use crate::dialogs;

#[derive(Clone)]
pub struct OpenMenu {
    repository: MenuItem<tauri::Wry>,
    demo: MenuItem<tauri::Wry>,
    busy: Arc<AtomicBool>,
}

impl OpenMenu {
    pub fn install(
        app: &AppHandle,
        window: &WebviewWindow,
        quit: Arc<AtomicBool>,
        tx: Sender<Option<SelectedSource>>,
    ) -> io::Result<Self> {
        let repository = MenuItem::with_id(
            app,
            "open-repository",
            "Open repository…",
            false,
            None::<&str>,
        )
        .map_err(io::Error::other)?;
        let demo = MenuItem::with_id(app, "open-demo", "Open demo", false, None::<&str>)
            .map_err(io::Error::other)?;
        let file = Submenu::with_items(app, "File", true, &[&repository, &demo])
            .map_err(io::Error::other)?;
        let menu = Menu::with_items(app, &[&file]).map_err(io::Error::other)?;
        app.set_menu(menu).map_err(io::Error::other)?;
        let controls = Self {
            repository,
            demo,
            busy: Arc::new(AtomicBool::new(true)),
        };
        let handler = controls.clone();
        let parent = window.clone();
        app.on_menu_event(move |app, event| {
            let own = match event.id().as_ref() {
                "open-repository" => true,
                "open-demo" => false,
                _ => return,
            };
            if quit.load(Ordering::SeqCst) || handler.busy.swap(true, Ordering::SeqCst) {
                return;
            }
            let (menu, parent, quit, tx) =
                (handler.clone(), parent.clone(), quit.clone(), tx.clone());
            let recovery = handler.clone();
            if app
                .run_on_main_thread(move || menu.select(&parent, own, &quit, &tx))
                .is_err()
            {
                let _ = recovery.enable(true);
            }
        });
        Ok(controls)
    }

    pub fn enable(&self, enabled: bool) -> io::Result<()> {
        // Keep the gate closed until both native menu updates have succeeded.
        self.busy.store(true, Ordering::SeqCst);
        self.repository
            .set_enabled(enabled)
            .map_err(io::Error::other)?;
        self.demo.set_enabled(enabled).map_err(io::Error::other)?;
        self.busy.store(!enabled, Ordering::SeqCst);
        Ok(())
    }

    fn select(
        &self,
        window: &WebviewWindow,
        own: bool,
        quit: &AtomicBool,
        tx: &Sender<Option<SelectedSource>>,
    ) {
        if quit.load(Ordering::SeqCst) {
            return;
        }
        let selected = if own {
            let Some(path) = rfd::FileDialog::new()
                .set_parent(window)
                .set_title("Open repository")
                .pick_folder()
            else {
                self.busy.store(false, Ordering::SeqCst);
                return;
            };
            match SelectedSource::validate(&path) {
                Ok(source) => Some(source),
                Err(error) => {
                    dialogs::message(
                        window,
                        "This repository could not be opened",
                        &format!("{error}\n\n{}", Refusal::UnsupportedLocation.remedy(&path)),
                    );
                    self.busy.store(false, Ordering::SeqCst);
                    return;
                }
            }
        } else {
            None
        };
        if quit.load(Ordering::SeqCst) || !dialogs::confirm(window) || quit.load(Ordering::SeqCst) {
            self.busy.store(false, Ordering::SeqCst);
            return;
        }
        if self.enable(false).is_ok() && tx.send(selected).is_ok() {
            return;
        }
        let _ = self.enable(true);
    }
}
