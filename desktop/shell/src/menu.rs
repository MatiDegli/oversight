use std::io;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, mpsc::Sender};

use oversight_desktop_core::refusal::Refusal;
use oversight_desktop_core::source::SelectedSource;
use oversight_desktop_core::switch::Request;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem, Submenu};
use tauri::{AppHandle, WebviewWindow};

use crate::{dialogs, settings};

#[derive(Clone, Copy)]
enum Item {
    Repository,
    Demo,
    Edit,
    Reload,
}

#[derive(Clone)]
pub struct OpenMenu {
    repository: MenuItem<tauri::Wry>,
    demo: MenuItem<tauri::Wry>,
    reload: MenuItem<tauri::Wry>,
    busy: Arc<AtomicBool>,
    app: PathBuf,
    data: PathBuf,
}

impl OpenMenu {
    pub fn install(
        app: &AppHandle,
        window: &WebviewWindow,
        quit: Arc<AtomicBool>,
        tx: Sender<Request>,
        payload: &Path,
        data: &Path,
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
        let separator = PredefinedMenuItem::separator(app).map_err(io::Error::other)?;
        let edit = MenuItem::with_id(app, "edit-settings", "Edit settings…", true, None::<&str>)
            .map_err(io::Error::other)?;
        let reload = MenuItem::with_id(
            app,
            "reload-settings",
            "Reload settings",
            false,
            None::<&str>,
        )
        .map_err(io::Error::other)?;
        let file = Submenu::with_items(
            app,
            "File",
            true,
            &[&repository, &demo, &separator, &edit, &reload],
        )
        .map_err(io::Error::other)?;
        let menu = Menu::with_items(app, &[&file]).map_err(io::Error::other)?;
        app.set_menu(menu).map_err(io::Error::other)?;
        let controls = Self {
            repository,
            demo,
            reload,
            busy: Arc::new(AtomicBool::new(true)),
            app: payload.into(),
            data: data.into(),
        };
        let handler = controls.clone();
        let parent = window.clone();
        app.on_menu_event(move |app, event| {
            handler.dispatch(app, event.id().as_ref(), &parent, &quit, &tx);
        });
        Ok(controls)
    }

    fn dispatch(
        &self,
        app: &AppHandle,
        id: &str,
        window: &WebviewWindow,
        quit: &Arc<AtomicBool>,
        tx: &Sender<Request>,
    ) {
        let item = match id {
            "open-repository" => Item::Repository,
            "open-demo" => Item::Demo,
            "edit-settings" => Item::Edit,
            "reload-settings" => Item::Reload,
            _ => return,
        };
        if quit.load(Ordering::SeqCst)
            || (!matches!(item, Item::Edit) && self.busy.swap(true, Ordering::SeqCst))
        {
            return;
        }
        let (menu, parent, quit, tx) = (self.clone(), window.clone(), quit.clone(), tx.clone());
        if app.run_on_main_thread(move || {
            if quit.load(Ordering::SeqCst) {
                return;
            }
            if matches!(item, Item::Edit) {
                if settings::edit(&menu.app, &menu.data).is_err() {
                    dialogs::message(&parent, "Settings could not be opened", "Check that the settings folder is writable and Notepad is available, then try again.");
                }
            } else {
                menu.select(&parent, item, &quit, &tx);
            }
        }).is_err() && !matches!(item, Item::Edit) {
            let _ = self.enable(true);
        }
    }

    pub fn enable(&self, enabled: bool) -> io::Result<()> {
        // Edit stays available; keep the switch gate closed until all three updates succeed.
        self.busy.store(true, Ordering::SeqCst);
        self.repository
            .set_enabled(enabled)
            .map_err(io::Error::other)?;
        self.demo.set_enabled(enabled).map_err(io::Error::other)?;
        self.reload.set_enabled(enabled).map_err(io::Error::other)?;
        self.busy.store(!enabled, Ordering::SeqCst);
        Ok(())
    }

    fn select(&self, window: &WebviewWindow, item: Item, quit: &AtomicBool, tx: &Sender<Request>) {
        if quit.load(Ordering::SeqCst) {
            return;
        }
        let request = if matches!(item, Item::Repository) {
            let Some(path) = rfd::FileDialog::new()
                .set_parent(window)
                .set_title("Open repository")
                .pick_folder()
            else {
                self.busy.store(false, Ordering::SeqCst);
                return;
            };
            match SelectedSource::validate(&path) {
                Ok(source) => Request::Open(Some(source)),
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
        } else if matches!(item, Item::Reload) {
            Request::Reload
        } else {
            Request::Open(None)
        };
        if quit.load(Ordering::SeqCst) {
            return;
        }
        let confirmed = if matches!(item, Item::Reload) {
            dialogs::confirm_reload(window)
        } else {
            dialogs::confirm(window)
        };
        if !confirmed || quit.load(Ordering::SeqCst) {
            self.busy.store(false, Ordering::SeqCst);
            return;
        }
        if self.enable(false).is_ok() && tx.send(request).is_ok() {
            return;
        }
        let _ = self.enable(true);
    }
}
