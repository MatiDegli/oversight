use std::io;
use std::path::PathBuf;
use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
};
use std::time::Duration;

use oversight_desktop_core::diagnostics::{Stage, finish};
use oversight_desktop_core::launch::{Launch, require_free_port};
use oversight_desktop_core::{PORT, URL};
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent};

use crate::{
    child::{Child, require_git},
    console,
    diagnostics::{Diagnostics, show_failure},
    listener, supervise,
};

pub fn run() -> Result<(), Box<dyn std::error::Error>> {
    let smoke = std::env::args_os().any(|arg| arg == "--smoke");
    let outcome = run_app();
    if outcome.is_err() {
        match Diagnostics::open() {
            Ok(diagnostic) => {
                let _ = diagnostic.stage(Stage::Desktop);
                diagnostic.failure(&io::Error::other("desktop initialization failed"), smoke);
            }
            Err(_) => {
                if !smoke {
                    show_failure(false);
                }
            }
        }
    }
    outcome
}

fn run_app() -> Result<(), Box<dyn std::error::Error>> {
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
                // The single-instance plugin has admitted this process before any log is replaced.
                let code = match Diagnostics::open() {
                    Ok(diagnostic) => match session(&handle, root, smoke, &setup_stop, &diagnostic)
                    {
                        Ok(()) => 0,
                        Err(error) => {
                            diagnostic.failure(&error, smoke);
                            1
                        }
                    },
                    Err(_) => {
                        if !smoke {
                            show_failure(false);
                        }
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
    diagnostic: &Diagnostics,
) -> io::Result<()> {
    diagnostic.stage(Stage::Git)?;
    require_git()?;
    diagnostic.stage(Stage::Console)?;
    console::install(stop.clone())?;
    let launch = if let Some(root) = root {
        diagnostic.stage(Stage::Launch)?;
        Launch::new(&root, smoke)?
    } else {
        diagnostic.stage(Stage::Executable)?;
        let executable = std::env::current_exe()?;
        diagnostic.stage(Stage::Payload)?;
        let app = oversight_desktop_core::installed::app_directory(&executable)?;
        let local = std::env::var_os("LOCALAPPDATA")
            .ok_or_else(|| io::Error::other("LOCALAPPDATA is required"))?;
        let data = PathBuf::from(local).join("Oversight");
        diagnostic.stage(Stage::Configuration)?;
        oversight_desktop_core::installed::initialize_data(&app, &data)?;
        diagnostic.stage(Stage::Launch)?;
        Launch::installed(&app, &data, smoke)?
    };
    diagnostic.stage(Stage::Listener)?;
    listener::require_unoccupied()?;
    diagnostic.stage(Stage::Port)?;
    require_free_port(PORT)?;
    diagnostic.stage(Stage::Spawn)?;
    let child = Child::spawn(&launch, diagnostic.tail.clone())?;
    let result = (|| {
        diagnostic.stage(Stage::Readiness)?;
        supervise::ready(&child, stop)?;
        if smoke {
            return Ok(());
        }
        diagnostic.stage(Stage::Navigation)?;
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
        diagnostic.stage(Stage::Running)?;
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
    diagnostic.backend_exit(child.exit_code());
    // A cleanup failure must not replace the phase/error that originally stopped startup.
    let phase = if result.is_ok() {
        diagnostic.stage(Stage::Cleanup)
    } else {
        Ok(())
    };
    let cleanup = child.close();
    child.drain_output();
    finish(result.and(phase), cleanup)?;
    diagnostic.stage(Stage::Complete)?;
    Ok(())
}
