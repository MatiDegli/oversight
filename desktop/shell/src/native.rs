use std::io;
use std::path::PathBuf;
use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
};

use oversight_desktop_core::diagnostics::Stage;
use tauri::{Manager, RunEvent, WindowEvent};

use crate::{
    console,
    diagnostics::{Diagnostics, show_failure},
    session::session,
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
            Err(_) if !smoke => show_failure(false),
            Err(_) => (),
        }
    }
    outcome
}

fn arguments() -> Result<(bool, Option<PathBuf>), Box<dyn std::error::Error>> {
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
    Ok((smoke, root))
}

fn run_app() -> Result<(), Box<dyn std::error::Error>> {
    let (smoke, root) = arguments()?;
    let quit = Arc::new(AtomicBool::new(false));
    let exiting = Arc::new(AtomicBool::new(false));
    let (setup_quit, setup_exiting) = (quit.clone(), exiting.clone());
    // Plugins, console and window are initialized once; only child supervision repeats.
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
                let code = match Diagnostics::open() {
                    Ok(mut diagnostic) => {
                        match session(&handle, root, smoke, setup_quit, &mut diagnostic) {
                            Ok(()) => 0,
                            Err(error) => {
                                diagnostic.failure(&error, smoke);
                                1
                            }
                        }
                    }
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
            let quit = quit.clone();
            move |_, event| match event {
                WindowEvent::CloseRequested { api, .. } => {
                    api.prevent_close();
                    quit.store(true, Ordering::SeqCst);
                }
                WindowEvent::Destroyed => quit.store(true, Ordering::SeqCst),
                _ => (),
            }
        })
        .build(tauri::generate_context!())?;
    app.run(move |_, event| {
        if let RunEvent::ExitRequested { api, code, .. } = event
            && !exiting.load(Ordering::SeqCst)
        {
            api.prevent_exit();
            if code.is_some() {
                quit.store(true, Ordering::SeqCst);
            }
        }
    });
    Ok(())
}
