use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};

use oversight_desktop_core::launch::{Launch, native_drive_path, require_free_port};

static NEXT: AtomicUsize = AtomicUsize::new(0);

struct CloneRoot(PathBuf);

impl CloneRoot {
    fn new() -> Self {
        let fixture_parent = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .join("target")
            .join("test-fixtures");
        let root = fixture_parent.join(format!(
            "oversight-desktop-{}-{}-space español 日本",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(root.join(".git")).unwrap();
        for relative in [
            "pyproject.toml",
            "backend/src/oversight/__main__.py",
            ".venv/pyvenv.cfg",
            ".venv/Scripts/python.exe",
            "frontend/dist/index.html",
        ] {
            let path = root.join(relative);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, "fixture; never executed").unwrap();
        }
        Self(root)
    }
}

impl Drop for CloneRoot {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.0).unwrap();
    }
}

#[cfg(windows)]
#[test]
fn fixture_root_is_a_native_drive_path() {
    let root = CloneRoot::new();
    assert!(root.0.to_str().is_some_and(native_drive_path));
}

#[cfg(windows)]
#[test]
fn launch_paths_remain_native_drive_paths() {
    let root = CloneRoot::new();
    let launch = Launch::new(&root.0, false).unwrap();
    assert!(launch.cwd.to_str().is_some_and(native_drive_path));
    assert!(launch.program.to_str().is_some_and(native_drive_path));
}

#[cfg(unix)]
#[test]
fn launch_preserves_validated_root_spelling() {
    let root = CloneRoot::new();
    let mut spelling = root.0.as_os_str().to_os_string();
    spelling.push("/.");
    let spelled_root = PathBuf::from(spelling);
    let launch = Launch::new(&spelled_root, false).unwrap();
    assert_eq!(launch.cwd.as_os_str(), spelled_root.as_os_str());
    assert_eq!(
        launch.program.as_os_str(),
        spelled_root
            .join(".venv")
            .join("Scripts")
            .join("python.exe")
            .as_os_str()
    );
}

#[test]
fn native_path_refuses_wsl_unc_device_relative_and_ambiguous_components() {
    for path in [
        "",
        "/mnt/c/clone",
        "\\\\wsl$\\Ubuntu\\clone",
        "\\\\server\\clone",
        "\\\\?\\C:\\clone",
        "C:clone",
        "C:/clone",
        "C:\\",
        "C:\\a\\..\\b",
        "C:\\a\\.\\b",
        "C:\\a\\",
        "C:\\a.\\b",
        "C:\\a \\b",
        "C:\\a:b",
        "C:\\a\n",
        "C:\\a\\\\b",
        "C:\\con\\clone",
        "C:\\aux.txt\\clone",
        "C:\\COM1\\clone",
        "C:\\lpt²\\clone",
    ] {
        assert!(!native_drive_path(path), "accepted {path:?}");
    }
    assert!(native_drive_path("C:\\private\\oversight"));
    assert!(native_drive_path("d:\\space español 日本\\oversight"));
}

#[test]
fn argv_preserves_spaces_and_unicode_without_shell_interpolation_or_reload() {
    let root = CloneRoot::new();
    let launch = Launch::new(&root.0, false).unwrap();
    assert_eq!(launch.cwd, root.0);
    assert_eq!(
        launch.program,
        root.0.join(".venv").join("Scripts").join("python.exe")
    );
    assert_eq!(launch.args, ["-m", "oversight", "serve"]);
    assert_eq!(launch.env.len(), 4);
    assert_eq!(launch.env["OVERSIGHT_HOST"], "127.0.0.1");
    assert_eq!(launch.env["OVERSIGHT_PORT"], "8710");
    assert_eq!(launch.env["OVERSIGHT_VITE_PORT"], "");
    assert_eq!(launch.env["OVERSIGHT_WORKSPACE_SOURCE"], "");
    assert!(launch.remove_env.contains(&"GIT_DIR"));
    assert!(launch.remove_env.contains(&"PYTHONPATH"));
    assert!(launch.remove_env.contains(&"OVERSIGHT_APP_ROOT"));
    assert!(launch.remove_env.contains(&"OVERSIGHT_ENV_FILE"));
}

#[test]
fn source_is_closed_and_smoke_always_uses_demo() {
    let root = CloneRoot::new();
    for source in [
        "C:\\private\\repo",
        "/tmp/repo",
        "boltons",
        "boltons-d31d821 ",
    ] {
        assert!(Launch::with_source(&root.0, false, source).is_err());
    }
    let pinned = Launch::with_source(&root.0, false, "boltons-d31d821").unwrap();
    assert_eq!(pinned.env["OVERSIGHT_WORKSPACE_SOURCE"], "boltons-d31d821");
    let smoke = Launch::with_source(&root.0, true, "boltons-d31d821").unwrap();
    assert_eq!(smoke.env["OVERSIGHT_WORKSPACE_SOURCE"], "");
}

#[test]
fn smoke_overrides_dotenv_keys_models_review_and_source_without_reading_secrets() {
    let root = CloneRoot::new();
    // Even a malformed secret file is irrelevant to argv construction.
    fs::write(root.0.join(".env"), [0xff]).unwrap();
    let launch = Launch::new(&root.0, true).unwrap();
    for key in [
        "META_API_KEY",
        "BASETEN_API_KEY",
        "OVERSIGHT_WORKSPACE_SOURCE",
    ] {
        assert_eq!(launch.env[key], "");
    }
    assert_eq!(launch.env["OVERSIGHT_CROSS_REVIEW"], "false");
    assert_eq!(launch.env["OVERSIGHT_MAX_AGENTS"], "2");
    assert_eq!(launch.env["OVERSIGHT_AGENT_MODEL"], "fake");
    assert_eq!(launch.env["OVERSIGHT_AGENT_MODEL_B"], "fake");
    assert_ne!(
        launch.env["OVERSIGHT_FAKE_SCRIPT"],
        launch.env["OVERSIGHT_FAKE_SCRIPT_B"]
    );
}

#[test]
fn refuses_relative_missing_and_non_file_prerequisites_before_launch() {
    assert!(Launch::new(Path::new("relative"), false).is_err());
    for relative in [
        ".git",
        "pyproject.toml",
        "backend/src/oversight/__main__.py",
        ".venv/pyvenv.cfg",
        ".venv/Scripts/python.exe",
        "frontend/dist/index.html",
    ] {
        let root = CloneRoot::new();
        assert!(Launch::new(&root.0, false).is_ok());
        let path = root.0.join(relative);
        if path.is_dir() {
            fs::remove_dir(&path).unwrap();
        } else {
            fs::remove_file(&path).unwrap();
        }
        assert!(
            Launch::new(&root.0, false).is_err(),
            "accepted missing {relative}"
        );
        fs::create_dir(&path).unwrap();
        if relative != ".git" {
            assert!(
                Launch::new(&root.0, false).is_err(),
                "accepted directory {relative}"
            );
        }
    }
}

#[cfg(unix)]
#[test]
fn refuses_symlinked_build_environment_root_and_ancestors() {
    use std::os::unix::fs::symlink;
    for relative in ["frontend/dist", ".venv", ".venv/Scripts/python.exe"] {
        let root = CloneRoot::new();
        let original = root.0.join(relative);
        let renamed = root.0.join("canary");
        fs::rename(&original, &renamed).unwrap();
        symlink(&renamed, &original).unwrap();
        assert!(Launch::new(&root.0, false).is_err());
        fs::remove_file(original).unwrap();
    }
    let root = CloneRoot::new();
    let alias = root.0.with_extension("alias");
    symlink(&root.0, &alias).unwrap();
    assert!(Launch::new(&alias, false).is_err());
    assert!(Launch::new(&alias.join("backend/.."), false).is_err());
    fs::remove_file(alias).unwrap();
}

#[test]
fn occupied_port_is_refused_without_attaching_or_disrupting_its_owner() {
    use std::net::{TcpListener, TcpStream};
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    assert!(require_free_port(port).is_err());
    assert!(TcpStream::connect(listener.local_addr().unwrap()).is_ok());
    assert!(require_free_port(0).is_err());
    drop(listener);
    assert!(require_free_port(port).is_ok());
}
