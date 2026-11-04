//! One window and a repeating, verified child lifecycle. No source-selection IPC.
use std::io;
use std::path::PathBuf;
use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
    mpsc::{self, Receiver},
};
use std::time::{Duration, Instant};

use oversight_desktop_core::PORT;
use oversight_desktop_core::diagnostics::Stage;
use oversight_desktop_core::launch::{Launch, require_free_port};
use oversight_desktop_core::lifecycle::Listener;
use oversight_desktop_core::refusal::Refusal;
use oversight_desktop_core::source::SelectedSource;
use oversight_desktop_core::switch::{Action, PAGE_LIMIT, Phase, Switch};
use tauri::AppHandle;

use crate::{
    child::{Child, require_git},
    console,
    diagnostics::Diagnostics,
    listener,
    menu::OpenMenu,
    supervise,
    window::Window,
};

enum Context {
    Clone(PathBuf),
    Installed { app: PathBuf, data: PathBuf },
}

impl Context {
    fn prepare(root: Option<PathBuf>, diagnostic: &Diagnostics) -> io::Result<Self> {
        if let Some(root) = root {
            return Ok(Self::Clone(root));
        }
        diagnostic.stage(Stage::Executable)?;
        let executable = std::env::current_exe()?;
        diagnostic.stage(Stage::Payload)?;
        let app = oversight_desktop_core::installed::app_directory(&executable)?;
        let local = std::env::var_os("LOCALAPPDATA")
            .ok_or_else(|| io::Error::other("LOCALAPPDATA is required"))?;
        let data = PathBuf::from(local).join("Oversight");
        diagnostic.stage(Stage::Configuration)?;
        oversight_desktop_core::installed::initialize_data(&app, &data)?;
        Ok(Self::Installed { app, data })
    }

    fn launch(&self, smoke: bool, source: Option<&SelectedSource>) -> io::Result<Launch> {
        match self {
            Self::Clone(root) if source.is_none() => Launch::new(root, smoke),
            Self::Clone(_) => Err(io::Error::other("own sources require installed mode")),
            Self::Installed { app, data } => Launch::installed_source(app, data, smoke, source),
        }
    }
}

struct Ui {
    window: Option<Window>,
    menu: Option<OpenMenu>,
    requests: Receiver<Option<SelectedSource>>,
}

impl Ui {
    fn new(
        app: &AppHandle,
        quit: Arc<AtomicBool>,
        smoke: bool,
        installed: bool,
    ) -> io::Result<Self> {
        let (tx, requests) = mpsc::channel();
        let window = if smoke { None } else { Some(Window::new(app)?) };
        let menu = if installed && let Some(window) = &window {
            Some(OpenMenu::install(app, &window.native, quit, tx)?)
        } else {
            None
        };
        Ok(Self {
            window,
            menu,
            requests,
        })
    }

    fn enable(&self, enabled: bool) -> io::Result<()> {
        if let Some(menu) = &self.menu {
            menu.enable(enabled)?;
        }
        Ok(())
    }

    fn message(&self, instruction: &str, content: &str) -> io::Result<()> {
        if let Some(window) = &self.window {
            window.message(instruction, content)?;
        }
        Ok(())
    }
}

pub fn session(
    app: &AppHandle,
    root: Option<PathBuf>,
    smoke: bool,
    quit: Arc<AtomicBool>,
    diagnostic: &mut Diagnostics,
) -> io::Result<()> {
    diagnostic.stage(Stage::Git)?;
    require_git()?;
    diagnostic.stage(Stage::Console)?;
    console::install(quit.clone())?;
    let context = Context::prepare(root, diagnostic)?;
    let ui = Ui::new(
        app,
        quit.clone(),
        smoke,
        matches!(context, Context::Installed { .. }),
    )?;
    let mut child = None;
    let result = supervise_loop(&context, &ui, smoke, &quit, diagnostic, &mut child);
    diagnostic.backend_exit(child.as_ref().and_then(Child::exit_code));
    stop(&mut child, result, &ui)?;
    diagnostic.stage(Stage::Complete)?;
    Ok(())
}

fn supervise_loop(
    context: &Context,
    ui: &Ui,
    smoke: bool,
    quit: &AtomicBool,
    diagnostic: &mut Diagnostics,
    child: &mut Option<Child>,
) -> io::Result<()> {
    let mut state = Switch::default();
    let mut source = None;
    loop {
        if quit.load(Ordering::SeqCst) {
            break;
        }
        let launch = context.launch(smoke, source.as_ref())?;
        start(&launch, diagnostic, child)?;
        let ready = supervise::ready(child.as_ref().unwrap(), quit);
        diagnostic.backend_exit(child.as_ref().and_then(Child::exit_code));
        if quit.load(Ordering::SeqCst) || smoke {
            state.quit();
            let primary = if quit.load(Ordering::SeqCst) {
                Ok(())
            } else {
                ready
            };
            stop(child, primary, ui)?;
            break;
        }
        if ready.is_err() {
            if ui.menu.is_none() {
                return ready;
            }
            // Refusals have no live backend; keep the inert window and allow another selection.
            if let Err(cleanup) = stop(child, Ok(()), ui) {
                return Err(io::Error::other(format!(
                    "startup: {ready:?}; cleanup: {cleanup:?}"
                )));
            }
            let refusal = diagnostic
                .tail
                .lock()
                .unwrap()
                .refusal()
                .unwrap_or(Refusal::Other);
            let runtime = PathBuf::from(&launch.env["OVERSIGHT_RUNTIME_DIR"]);
            ui.message(
                "This session could not be opened",
                &refusal.remedy(&runtime),
            )?;
            state.startup(false);
        } else {
            ensure_owned(child.as_ref().unwrap())?;
            if let Some(window) = &ui.window {
                window.backend()?;
            }
            state.startup(true);
        }
        ui.enable(true)?;
        if !wait_for_switch(ui, quit, &mut state, child)? {
            break;
        }
        source = state
            .take_source()
            .ok_or_else(|| io::Error::other("missing confirmed source"))?;
    }
    Ok(())
}

fn start(
    launch: &Launch,
    diagnostic: &mut Diagnostics,
    child: &mut Option<Child>,
) -> io::Result<()> {
    diagnostic.begin_child();
    diagnostic.stage(Stage::Listener)?;
    listener::require_unoccupied()?;
    diagnostic.stage(Stage::Port)?;
    require_free_port(PORT)?;
    diagnostic.stage(Stage::Spawn)?;
    *child = Some(Child::spawn(launch, diagnostic.tail.clone())?);
    diagnostic.stage(Stage::Readiness)?;
    Ok(())
}

fn ensure_owned(child: &Child) -> io::Result<()> {
    if !child.alive() || listener::observe(child)? != Listener::Owned {
        return Err(io::Error::other(
            "backend stopped or listener ownership lost",
        ));
    }
    Ok(())
}

fn wait_for_switch(
    ui: &Ui,
    quit: &AtomicBool,
    state: &mut Switch,
    child: &mut Option<Child>,
) -> io::Result<bool> {
    let began = Instant::now();
    loop {
        if quit.load(Ordering::SeqCst) {
            state.quit();
            stop(child, Ok(()), ui)?;
            state.closed(true, true);
            return Ok(false);
        }
        if let Some(active) = child.as_ref()
            && let Err(error) = ensure_owned(active)
        {
            stop(child, Err(error), ui)?;
        }
        if let Ok(source) = ui.requests.try_recv()
            && state.confirm(source, began.elapsed()) == Action::LoadPage
        {
            ui.enable(false)?;
            if load_inert(ui, quit, state, began)? {
                stop(child, Ok(()), ui)?;
                if quit.load(Ordering::SeqCst) {
                    state.quit();
                }
                return Ok(state.closed(true, true) == Action::Launch);
            }
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}

fn load_inert(ui: &Ui, quit: &AtomicBool, state: &mut Switch, began: Instant) -> io::Result<bool> {
    let window = ui
        .window
        .as_ref()
        .ok_or_else(|| io::Error::other("missing switch window"))?;
    let previous = window.switching();
    loop {
        if quit.load(Ordering::SeqCst) {
            state.quit();
            return Ok(true);
        }
        let loaded = previous
            .as_ref()
            .is_ok_and(|old| window.finished.load(Ordering::SeqCst) > *old);
        let action = if previous.is_err() {
            state.page(began.elapsed() + PAGE_LIMIT, false)
        } else {
            state.page(began.elapsed(), loaded)
        };
        match action {
            Action::Close => return Ok(true),
            Action::PageTimeout => {
                if state.phase() == Phase::Ready {
                    window.backend()?;
                }
                ui.message("The switch could not start", "The switching page did not finish loading within five seconds. The current session is still running. Try Open again.")?;
                ui.enable(true)?;
                return Ok(false);
            }
            _ => std::thread::sleep(Duration::from_millis(20)),
        }
    }
}

/// The child and its Job handles are dropped only after close/reap evidence is collected.
fn stop(child: &mut Option<Child>, primary: io::Result<()>, ui: &Ui) -> io::Result<()> {
    let outcome = supervise::close(child.take(), primary);
    if outcome.unverified() {
        let _ = ui.message("The session could not be stopped", "Oversight must close because shutdown could not be verified. Preserve retained state before opening the repository again.");
    }
    outcome.finish()
}
