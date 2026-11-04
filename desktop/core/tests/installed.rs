use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};

use oversight_desktop_core::installed::{ASSETS, app_directory, initialize_data};
use oversight_desktop_core::launch::Launch;

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
fn installed_arguments_and_paths_are_separate_and_preserve_unicode() {
    let layout = Layout::new();
    initialize_data(&layout.app(), &layout.data()).unwrap();
    let launch = Launch::installed(&layout.app(), &layout.data(), true).unwrap();
    assert_eq!(launch.cwd, layout.app());
    assert_eq!(launch.program, layout.app().join("python/python.exe"));
    assert_eq!(
        launch.args,
        [
            "-m".into(),
            "oversight".into(),
            "serve".into(),
            "--demo-python".into(),
            layout.app().join("demo-python/python.exe").into_os_string()
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
    initialize_data(&layout.app(), &layout.data()).unwrap();
    let config = layout.data().join(".env");
    assert_eq!(fs::read(&config).unwrap(), b"blank template");
    fs::write(&config, [0xff, 0xfe]).unwrap();
    initialize_data(&layout.app(), &layout.data()).unwrap();
    assert_eq!(fs::read(config).unwrap(), [0xff, 0xfe]);
    assert!(initialize_data(&layout.app(), &layout.app().join("data")).is_err());
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
    let python = layout.app().join("python/python.exe");
    fs::remove_file(&python).unwrap();
    symlink(layout.app().join("demo-python/python.exe"), &python).unwrap();
    assert!(Launch::installed(&layout.app(), &layout.data(), false).is_err());
}
