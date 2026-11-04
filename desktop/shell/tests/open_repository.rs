//! Headless installed lifecycle evidence. Native dialogs/rendering need headed acceptance.
#![cfg(windows)]
#![allow(dead_code)]

#[path = "../src/child.rs"]
mod child;
#[path = "../src/console.rs"]
mod console;
#[path = "../src/listener.rs"]
mod listener;
#[path = "../src/output.rs"]
mod output;
#[path = "../src/supervise.rs"]
mod supervise;

use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex, atomic::AtomicBool};
use std::time::{Duration, Instant};

use oversight_desktop_core::PORT;
use oversight_desktop_core::diagnostics::OutputTail;
use oversight_desktop_core::launch::{Launch, require_free_port};
use oversight_desktop_core::refusal::Refusal;
use oversight_desktop_core::source::SelectedSource;
use oversight_desktop_core::switch::{Action, Switch};

struct Session {
    child: Option<child::Child>,
    tail: Arc<Mutex<OutputTail>>,
    runtime: PathBuf,
    ready: bool,
}

fn fixture(app: &Path, operation: &str, root: &Path) {
    let script = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixture.py");
    let mut process = Command::new(app.join("python").join("python.exe"))
        .args([script.as_os_str(), operation.as_ref(), root.as_os_str()])
        .stdin(Stdio::null())
        .spawn()
        .unwrap();
    let deadline = Instant::now() + Duration::from_secs(660);
    loop {
        if let Some(status) = process.try_wait().unwrap() {
            assert!(status.success(), "fixture preparation failed");
            return;
        }
        if Instant::now() >= deadline {
            process.kill().unwrap();
            process.wait().unwrap();
            panic!("fixture preparation exceeded deadline");
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}

fn open(app: &Path, data: &Path, source: Option<&SelectedSource>, quit: &AtomicBool) -> Session {
    let mut launch = Launch::installed_source(app, data, false, source).unwrap();
    // Use smoke's scripted transport overrides without its mandatory demo-source override.
    let fake = Launch::installed(app, data, true).unwrap();
    for (key, value) in fake.env {
        if key.starts_with("META_")
            || key.starts_with("BASETEN_")
            || key.starts_with("OVERSIGHT_AGENT_")
            || key.starts_with("OVERSIGHT_FAKE_")
            || matches!(key, "OVERSIGHT_CROSS_REVIEW" | "OVERSIGHT_MAX_AGENTS")
        {
            launch.env.insert(key, value);
        }
    }
    listener::require_unoccupied().unwrap();
    require_free_port(PORT).unwrap();
    let tail = Arc::new(Mutex::new(OutputTail::new(app.into(), data.into())));
    let child = child::Child::spawn(&launch, tail.clone()).unwrap();
    let ready = supervise::ready(&child, quit).is_ok();
    Session {
        child: Some(child),
        tail,
        runtime: launch.env["OVERSIGHT_RUNTIME_DIR"].clone().into(),
        ready,
    }
}

fn switch(
    app: &Path,
    data: &Path,
    source: Option<SelectedSource>,
    current: &mut Session,
    state: &mut Switch,
    quit: &AtomicBool,
) {
    assert_eq!(state.confirm(source, Duration::ZERO), Action::LoadPage);
    // Headless harness supplies page completion; the real window supplies on_page_load Finished.
    assert_eq!(state.page(Duration::from_secs(1), true), Action::Close);
    let cleanup = supervise::close(current.child.take(), Ok(()));
    assert_eq!(
        state.closed(cleanup.is_ok(), cleanup.is_ok()),
        Action::Launch
    );
    cleanup.unwrap();
    let selected = state.take_source().unwrap();
    *current = open(app, data, selected.as_ref(), quit);
    state.startup(current.ready);
}

fn provenance(session: &Session, source: &SelectedSource, name: &str) {
    assert!(session.ready, "installed own source did not become ready");
    let baseline = fs::read_to_string(session.runtime.join("own-source")).unwrap();
    assert!(
        baseline
            .split('\0')
            .any(|line| Path::new(line) == source.path())
    );
    let expected = format!("value = '{name}'\n");
    assert_eq!(
        fs::read_to_string(source.path().join("fixture_value.py")).unwrap(),
        expected
    );
    assert_eq!(
        fs::read_to_string(session.runtime.join("workspace").join("fixture_value.py")).unwrap(),
        expected
    );
}

fn prepared_fixtures() -> (PathBuf, PathBuf, PathBuf, SelectedSource, SelectedSource) {
    let local = PathBuf::from(std::env::var_os("LOCALAPPDATA").unwrap());
    let app = local.join("Programs").join("Oversight").join("app");
    let data = local.join("Oversight");
    assert!(app.join("python").join("python.exe").is_file());
    let root = local.join(format!("or-{}", std::process::id()));
    assert!(
        !root.starts_with(
            Path::new(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .unwrap()
                .parent()
                .unwrap()
        )
    );
    assert!(!root.exists());
    fixture(&app, "setup", &root);
    let one = SelectedSource::validate(&root.join("one")).unwrap();
    let two = SelectedSource::validate(&root.join("two")).unwrap();
    (app, data, root, one, two)
}

#[test]
#[ignore = "requires the current-user installed payload, Git and restored build PATH"]
fn installed_open_repository() {
    let (app, data, root, one, two) = prepared_fixtures();
    let quit = Arc::new(AtomicBool::new(false));
    console::install(quit.clone()).unwrap();
    let mut state = Switch::default();
    let mut current = open(&app, &data, Some(&one), &quit);
    state.startup(current.ready);
    provenance(&current, &one, "one");
    switch(&app, &data, None, &mut current, &mut state, &quit);
    assert!(current.ready);
    assert!(current.runtime.join("workspace").join("shop").is_dir());
    switch(
        &app,
        &data,
        Some(one.clone()),
        &mut current,
        &mut state,
        &quit,
    );
    provenance(&current, &one, "one");
    switch(
        &app,
        &data,
        Some(two.clone()),
        &mut current,
        &mut state,
        &quit,
    );
    provenance(&current, &two, "two");
    fixture(&app, "advance", &current.runtime.join("workspace"));
    switch(&app, &data, None, &mut current, &mut state, &quit);
    assert!(current.ready);
    switch(
        &app,
        &data,
        Some(two.clone()),
        &mut current,
        &mut state,
        &quit,
    );
    assert!(!current.ready);
    supervise::close(current.child.take(), Ok(())).unwrap();
    assert_eq!(
        current.tail.lock().unwrap().refusal(),
        Some(Refusal::PreviousSessionChanged)
    );
    // Every attempt must supply its category again, independent of earlier output.
    switch(
        &app,
        &data,
        Some(two.clone()),
        &mut current,
        &mut state,
        &quit,
    );
    assert!(!current.ready);
    supervise::close(current.child.take(), Ok(())).unwrap();
    assert_eq!(
        current.tail.lock().unwrap().refusal(),
        Some(Refusal::PreviousSessionChanged)
    );
    console::completed();
    for source in [one, two] {
        fs::remove_dir_all(source.runtime_dir(&data).parent().unwrap()).unwrap();
    }
    fs::remove_dir_all(root).unwrap();
    println!("Installed own-source readiness/provenance, demo/back and moved-main refusal passed.");
}
