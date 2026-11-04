use std::collections::BTreeMap;
use std::ffi::OsString;
use std::fs;
use std::io;
use std::net::{Ipv4Addr, SocketAddrV4, TcpListener};
use std::path::{Component, Path, PathBuf};

use crate::{HOST, PORT};

/// Passed directly as argv/env to the platform adapter; never through a shell.
#[derive(Debug)]
pub struct Launch {
    pub cwd: PathBuf,
    pub program: PathBuf,
    pub args: Vec<OsString>,
    pub env: BTreeMap<&'static str, String>,
    pub remove_env: [&'static str; 9],
}

impl Launch {
    pub fn new(root: &Path, smoke: bool) -> io::Result<Self> {
        Self::with_source(root, smoke, "")
    }

    pub fn with_source(root: &Path, smoke: bool, source: &str) -> io::Result<Self> {
        if !matches!(source, "" | "boltons-d31d821") {
            return Err(invalid(
                "desktop source must be demo or the exact pinned profile",
            ));
        }
        validate_root(root)?;
        Ok(Self::for_program(
            root,
            root.join(".venv/Scripts/python.exe"),
            smoke,
            source,
        ))
    }

    pub(crate) fn for_program(root: &Path, program: PathBuf, smoke: bool, source: &str) -> Self {
        let mut env = BTreeMap::from([
            ("OVERSIGHT_HOST", HOST.to_owned()),
            ("OVERSIGHT_PORT", PORT.to_string()),
            ("OVERSIGHT_VITE_PORT", String::new()),
            ("OVERSIGHT_WORKSPACE_SOURCE", source.to_owned()),
        ]);
        if smoke {
            env.extend([
                ("META_API_KEY", String::new()),
                ("BASETEN_API_KEY", String::new()),
                ("OVERSIGHT_CROSS_REVIEW", "false".into()),
                ("OVERSIGHT_WORKSPACE_SOURCE", String::new()),
                ("OVERSIGHT_MAX_AGENTS", "2".into()),
                ("OVERSIGHT_AGENT_MODEL", "fake".into()),
                ("OVERSIGHT_AGENT_MODEL_B", "fake".into()),
                (
                    "OVERSIGHT_FAKE_SCRIPT",
                    "fixtures/fake-model/demo-l2-coupon.json".into(),
                ),
                (
                    "OVERSIGHT_FAKE_SCRIPT_B",
                    "fixtures/fake-model/demo-l2-restock.json".into(),
                ),
            ]);
        }
        Self {
            // Validation already excludes traversal and links. Canonicalizing on
            // Windows would replace these native drive paths with device paths.
            cwd: root.to_path_buf(),
            program,
            args: vec!["-m".into(), "oversight".into(), "serve".into()],
            env,
            remove_env: [
                "GIT_DIR",
                "GIT_WORK_TREE",
                "GIT_INDEX_FILE",
                "GIT_OBJECT_DIRECTORY",
                "GIT_COMMON_DIR",
                "GIT_PREFIX",
                "PYTHONPATH",
                "OVERSIGHT_APP_ROOT",
                "OVERSIGHT_ENV_FILE",
            ],
        }
    }
}

/// A native adapter must reject UNC, device and WSL roots before filesystem I/O.
/// This lexical check is portable so those refusals are tested on Linux too.
pub fn native_drive_path(value: &str) -> bool {
    let bytes = value.as_bytes();
    bytes.len() > 3
        && bytes[0].is_ascii_alphabetic()
        && bytes[1] == b':'
        && bytes[2] == b'\\'
        && !value[3..].split('\\').any(|part| {
            part.is_empty()
                || part == "."
                || part == ".."
                || part.ends_with([' ', '.'])
                || reserved_name(part)
                || part.chars().any(|ch| ch < ' ' || "<>:\"/|?*".contains(ch))
        })
}

fn reserved_name(part: &str) -> bool {
    let name = part.split('.').next().unwrap_or("").to_ascii_uppercase();
    matches!(name.as_str(), "CON" | "PRN" | "AUX" | "NUL")
        || ["COM", "LPT"].iter().any(|prefix| {
            name.strip_prefix(prefix).is_some_and(|suffix| {
                matches!(
                    suffix,
                    "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9" | "¹" | "²" | "³"
                )
            })
        })
}

pub(crate) fn invalid(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, message)
}

pub(crate) fn plain(path: &Path) -> io::Result<fs::Metadata> {
    let metadata = fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink() || is_reparse(&metadata) {
        return Err(invalid("clone prerequisites must not traverse links"));
    }
    Ok(metadata)
}

#[cfg(windows)]
fn is_reparse(metadata: &fs::Metadata) -> bool {
    use std::os::windows::fs::MetadataExt;
    metadata.file_attributes() & 0x400 != 0
}

#[cfg(not(windows))]
fn is_reparse(_: &fs::Metadata) -> bool {
    false
}

pub(crate) fn validate_directory(root: &Path) -> io::Result<()> {
    if !root.is_absolute() || root.components().any(|part| part == Component::ParentDir) {
        return Err(invalid("provide an absolute native clone root"));
    }
    #[cfg(windows)]
    if !root.to_str().is_some_and(native_drive_path) {
        return Err(invalid(
            "provide a native local drive path; no WSL or UNC paths",
        ));
    }
    let mut ancestor = PathBuf::new();
    for part in root.components() {
        ancestor.push(part);
        if !ancestor.is_absolute() {
            continue;
        }
        if !plain(&ancestor)?.is_dir() {
            return Err(invalid("clone root must be a directory"));
        }
    }
    Ok(())
}

pub fn validate_root(root: &Path) -> io::Result<()> {
    validate_directory(root)?;
    // Require a clone, not a product worktree. A synced environment is prepared
    // by uv, never by this library. Existence cannot prove its frozen contents.
    for relative in [
        ".git",
        "pyproject.toml",
        "backend/src/oversight/__main__.py",
        ".venv/pyvenv.cfg",
        ".venv/Scripts/python.exe",
        "frontend/dist/index.html",
    ] {
        let mut path = root.to_path_buf();
        for part in Path::new(relative).components() {
            path.push(part);
            plain(&path)?;
        }
        let metadata = plain(&path)?;
        if (relative == ".git" && !metadata.is_dir()) || (relative != ".git" && !metadata.is_file())
        {
            return Err(invalid(
                "missing native clone, synced .venv or frontend build",
            ));
        }
    }
    Ok(())
}

/// Preflight only: release immediately before spawn. Listener ownership must
/// still be established after spawn to close the bind/spawn race safely.
pub fn require_free_port(port: u16) -> io::Result<()> {
    if port == 0 {
        return Err(invalid("port must be between 1 and 65535"));
    }
    let _reservation = TcpListener::bind(SocketAddrV4::new(Ipv4Addr::LOCALHOST, port))?;
    Ok(())
}
