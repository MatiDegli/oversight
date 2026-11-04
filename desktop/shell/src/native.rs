use std::io;
use std::path::PathBuf;
use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
};
use std::time::Duration;

use oversight_desktop_core::launch::{Launch, require_free_port};
use oversight_desktop_core::{PORT, URL};
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent};

use crate::{
    child::{Child, require_git},
    console, listener, supervise,
};

pub fn run() -> Result<(), Box<dyn std::error::Error>> {
    let mut smoke = false;
    let mut root = None;
    let mut args = std::env::args_os().skip(1);
    while let Some(arg) = args.next() {
        if arg == "--smoke" {
            smoke = true;
        } else if arg == "--clone-root" {
            root = Some(PathBuf::from(args.next().ok_or("missing clone root")?));
        } else {
            return Err("expected --smoke or --clone-root PATH".into());
        }
    }
    let stop = Arc::new(AtomicBool::new(false));
    let exiting = Arc::new(AtomicBool::new(false));
    let setup_stop = stop.clone();
    let setup_exiting = exiting.clone();
    // Register single-instance before setup can create any child, including smoke.
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .setup(move |app| {
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                let outcome = session(&handle, root, smoke, &setup_stop);
                let code = match outcome {
                    Ok(()) => 0,
                    Err(error) => {
                        eprintln!("Oversight desktop: {error}");
                        1
                    }
                };
                console::completed();
                setup_exiting.store(true, Ordering::SeqCst);
                handle.exit(code);
            });
            Ok(())
        })
        .on_window_event({
            let stop = stop.clone();
            move |_, event| match event {
                WindowEvent::CloseRequested { api, .. } => {
                    api.prevent_close();
                    stop.store(true, Ordering::SeqCst);
                }
                WindowEvent::Destroyed => stop.store(true, Ordering::SeqCst),
                _ => (),
            }
        })
        .build(tauri::generate_context!())?;
    app.run(move |_, event| {
        if let RunEvent::ExitRequested { api, code, .. } = event {
            if !exiting.load(Ordering::SeqCst) {
                api.prevent_exit();
                if code.is_some() {
                    stop.store(true, Ordering::SeqCst);
                }
            }
        }
    });
    Ok(())
}

fn session(
    app: &tauri::AppHandle,
    root: Option<PathBuf>,
    smoke: bool,
    stop: &Arc<AtomicBool>,
) -> io::Result<()> {
    require_git()?;
    console::install(stop.clone())?;
    let launch = if let Some(root) = root {
        Launch::new(&root, smoke)?
    } else {
        let app = oversight_desktop_core::installed::app_directory(&std::env::current_exe()?)?;
        let local = std::env::var_os("LOCALAPPDATA")
            .ok_or_else(|| io::Error::other("LOCALAPPDATA is required"))?;
        let data = PathBuf::from(local).join("Oversight");
        oversight_desktop_core::installed::initialize_data(&app, &data)?;
        Launch::installed(&app, &data, smoke)?
    };
    listener::require_unoccupied()?;
    require_free_port(PORT)?;
    let child = Child::spawn(&launch)?;
    let result = (|| {
        supervise::ready(&child, stop)?;
        if smoke {
            return Ok(());
        }
        if !child.alive()
            || listener::observe(&child)? != oversight_desktop_core::lifecycle::Listener::Owned
        {
            return Err(io::Error::other("backend ownership lost before navigation"));
        }
        WebviewWindowBuilder::new(app, "main", WebviewUrl::External(URL.parse().unwrap()))
            .title("Oversight")
            .inner_size(1280.0, 820.0)
            .on_navigation(|url| url.origin().ascii_serialization() == "http://127.0.0.1:8710")
            .build()
            .map_err(io::Error::other)?;
        while !stop.load(Ordering::SeqCst) {
            if !child.alive()
                || listener::observe(&child)? != oversight_desktop_core::lifecycle::Listener::Owned
            {
                return Err(io::Error::other(
                    "backend stopped or listener ownership lost",
                ));
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        Ok(())
    })();
    let graceful = child.close()?;
    result?;
    if !graceful {
        return Err(io::Error::other("backend required forced cleanup"));
    }
    Ok(())
}
