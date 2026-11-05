use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};

use oversight_desktop_core::installed::{
    ASSETS, app_directory, initialize_data, notepad_path, settings_file,
};
use oversight_desktop_core::launch::Launch;
#[cfg(windows)]
use oversight_desktop_core::launch::native_drive_path;

static NEXT: AtomicUsize = AtomicUsize::new(0);
struct Layout(PathBuf);
impl Layout {
    fn new() -> Self {
        let root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .join("target")
            .join("test-fixtures")
            .join(format!(
                "installed-{}-{} español 日本",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
        for file in ASSETS {
            let path = root.join("app").join(file);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, "blank template").unwrap();
        }
        Self(root)
    }
    fn app(&self) -> PathBuf {
        self.0.join("app")
    }
    fn data(&self) -> PathBuf {
        self.0.join("data")
    }
}
impl Drop for Layout {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.0).unwrap();
    }
}

#[test]
fn installed_app_and_data_accept_profile_names_containing_tildes() {
    use oversight_desktop_core::source::SelectedSource;
    let layout = Layout::new();
    let local = layout
        .0
        .join("Users")
        .join("ana~dev")
        .join("AppData")
        .join("Local");
    let install = local.join("Programs").join("Oversight");
    fs::create_dir_all(&install).unwrap();
    let app = install.join("app");
    let data = local.join("Oversight");
    fs::rename(layout.app(), &app).unwrap();
    // Both initialization and executable-relative resolution reach validate_directory.
    initialize_data(&app, &data).unwrap();
    assert_eq!(
        app_directory(&install.join("oversight-desktop.exe")).unwrap(),
        app
    );
    assert!(SelectedSource::validate(Path::new(r"C:\Users\ana~dev\repository")).is_err());
    let source = SelectedSource::validate(Path::new(r"C:\Users\ana\repository")).unwrap();
    for selected in [None, Some(&source)] {
        let launch = Launch::installed_source(&app, &data, false, selected).unwrap();
        assert_eq!(launch.cwd, app);
        assert_eq!(
            launch.env["OVERSIGHT_ENV_FILE"],
            data.join(".env").to_str().unwrap()
        );
        assert_eq!(
            launch.env["OVERSIGHT_RUNTIME_DIR"],
            oversight_desktop_core::source::runtime_dir(&data, selected)
                .to_str()
                .unwrap()
        );
    }
}

#[test]
fn installed_arguments_and_paths_are_separate_and_preserve_unicode() {
    let layout = Layout::new();
    initialize_data(&layout.app(), &layout.data()).unwrap();
    let launch = Launch::installed(&layout.app(), &layout.data(), true).unwrap();
    assert_eq!(launch.cwd, layout.app());
    assert_eq!(
        launch.program,
        layout.app().join("python").join("python.exe")
    );
    #[cfg(windows)]
    {
        assert!(launch.cwd.to_str().is_some_and(native_drive_path));
        assert!(launch.program.to_str().is_some_and(native_drive_path));
        assert!(
            launch
                .args
                .last()
                .unwrap()
                .to_str()
                .is_some_and(native_drive_path)
        );
    }
    assert_eq!(
        launch.args,
        [
            "-m".into(),
            "oversight".into(),
            "serve".into(),
            "--demo-python".into(),
            layout
                .app()
                .join("demo-python")
                .join("python.exe")
                .into_os_string()
        ]
    );
    for (name, path) in [
        ("OVERSIGHT_APP_ROOT", layout.app()),
        ("OVERSIGHT_ENV_FILE", layout.data().join(".env")),
        ("OVERSIGHT_RUNTIME_DIR", layout.data().join(".runtime")),
    ] {
        assert_eq!(launch.env[name], path.to_str().unwrap());
    }
    assert_eq!(launch.env["META_API_KEY"], "");
    assert_eq!(launch.env["BASETEN_API_KEY"], "");
    assert_eq!(launch.env["OVERSIGHT_WORKSPACE_SOURCE"], "");
    assert_eq!(
        app_directory(&layout.0.join("oversight-desktop.exe")).unwrap(),
        layout.app()
    );
    assert!(!layout.app().join(".git").exists());
}

#[test]
fn configuration_is_created_once_and_never_read_or_overwritten() {
    let layout = Layout::new();
    assert!(initialize_data(&layout.app(), &layout.data()).unwrap());
    let config = layout.data().join(".env");
    assert_eq!(fs::read(&config).unwrap(), b"blank template");
    fs::write(&config, [0xff, 0xfe]).unwrap();
    assert!(!initialize_data(&layout.app(), &layout.data()).unwrap());
    assert_eq!(fs::read(config).unwrap(), [0xff, 0xfe]);
    assert!(initialize_data(&layout.app(), &layout.app().join("data")).is_err());
}

#[test]
fn missing_settings_are_recreated_without_overwriting_saved_settings() {
    let layout = Layout::new();
    assert!(initialize_data(&layout.app(), &layout.data()).unwrap());
    let config = settings_file(&layout.data());
    fs::remove_file(&config).unwrap();
    assert!(initialize_data(&layout.app(), &layout.data()).unwrap());
    assert_eq!(fs::read(&config).unwrap(), b"blank template");
    fs::write(&config, b"saved settings").unwrap();
    assert!(!initialize_data(&layout.app(), &layout.data()).unwrap());
    assert_eq!(fs::read(&config).unwrap(), b"saved settings");
    let launch = Launch::installed(&layout.app(), &layout.data(), false).unwrap();
    assert_eq!(launch.env["OVERSIGHT_ENV_FILE"], config.to_str().unwrap());
}

#[test]
fn notepad_is_built_only_from_the_supplied_absolute_system_directory() {
    let system = if cfg!(windows) {
        Path::new(r"C:\Windows folder\System32")
    } else {
        Path::new("/Windows folder/System32")
    };
    assert_eq!(notepad_path(system).unwrap(), system.join("notepad.exe"));
    for invalid in [Path::new(""), Path::new("notepad.exe"), &system.join("..")] {
        assert!(notepad_path(invalid).is_err());
    }
}

#[test]
fn every_packaged_prerequisite_is_required() {
    for file in ASSETS {
        let layout = Layout::new();
        fs::remove_file(layout.app().join(file)).unwrap();
        assert!(
            initialize_data(&layout.app(), &layout.data()).is_err(),
            "{file}"
        );
    }
}

#[cfg(unix)]
#[test]
fn links_in_payload_or_existing_configuration_are_refused() {
    use std::os::unix::fs::symlink;
    let layout = Layout::new();
    initialize_data(&layout.app(), &layout.data()).unwrap();
    let config = layout.data().join(".env");
    fs::remove_file(&config).unwrap();
    symlink(layout.app().join("config.env.example"), &config).unwrap();
    assert!(initialize_data(&layout.app(), &layout.data()).is_err());
    let python = layout.app().join("python").join("python.exe");
    fs::remove_file(&python).unwrap();
    symlink(layout.app().join("demo-python").join("python.exe"), &python).unwrap();
    assert!(Launch::installed(&layout.app(), &layout.data(), false).is_err());
}

#[test]
fn every_launch_overrides_inherited_source_runtime_ledger_and_discard() {
    use oversight_desktop_core::source::SelectedSource;
    let layout = Layout::new();
    initialize_data(&layout.app(), &layout.data()).unwrap();
    let source = SelectedSource::validate(Path::new(r"C:\private\repo")).unwrap();
    for (selected, smoke) in [(None, false), (Some(&source), false), (Some(&source), true)] {
        let launch =
            Launch::installed_source(&layout.app(), &layout.data(), smoke, selected).unwrap();
        let mut inherited = std::collections::BTreeMap::from([
            ("OVERSIGHT_WORKSPACE_SOURCE", "hostile".to_owned()),
            ("OVERSIGHT_RUNTIME_DIR", "elsewhere".to_owned()),
            ("OVERSIGHT_LEDGER_DIR", "elsewhere".to_owned()),
            (
                "OVERSIGHT_DISCARD_OWN_RUNTIME",
                "authorize-discard".to_owned(),
            ),
        ]);
        inherited.extend(launch.env);
        let effective = if smoke { None } else { selected };
        assert_eq!(
            inherited["OVERSIGHT_WORKSPACE_SOURCE"],
            effective.map(|s| s.path().to_str().unwrap()).unwrap_or("")
        );
        assert_eq!(
            inherited["OVERSIGHT_RUNTIME_DIR"],
            oversight_desktop_core::source::runtime_dir(&layout.data(), effective)
                .to_str()
                .unwrap()
        );
        assert_eq!(
            inherited["OVERSIGHT_LEDGER_DIR"],
            layout.data().join(".runtime").to_str().unwrap()
        );
        assert_eq!(inherited["OVERSIGHT_DISCARD_OWN_RUNTIME"], "");
    }
}
