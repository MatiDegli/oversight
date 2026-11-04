//! Installed payload validation and write-once configuration; no secret reads.
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};

use crate::launch::{Launch, invalid, plain, validate_directory};

pub const ASSETS: &[&str] = &[
    "python/python.exe",
    "demo-python/python.exe",
    "frontend/dist/index.html",
    "demo-repo/pyproject.toml",
    "fixtures/layouts/demo-repo.json",
    "fixtures/fake-model/demo-l2-coupon.json",
    "fixtures/fake-model/demo-l2-restock.json",
    "tools/demo-venv-requirements.txt",
    "config.env.example",
    "payload.json",
    "THIRD_PARTY_NOTICES.md",
];

fn payload(app: &Path) -> io::Result<()> {
    validate_directory(app)?;
    for relative in ASSETS {
        let mut path = app.to_path_buf();
        for part in Path::new(relative).components() {
            path.push(part);
            plain(&path)?;
        }
        if !plain(&path)?.is_file() {
            return Err(invalid("installed payload asset must be a plain file"));
        }
    }
    Ok(())
}

/// The shell calls this only after resolving the executable's own installation.
/// Existing .env values are never opened; create_new also closes the first-launch race.
pub fn initialize_data(app: &Path, data: &Path) -> io::Result<()> {
    payload(app)?;
    if data.starts_with(app) || app.starts_with(data) || !data.is_absolute() {
        return Err(invalid(
            "data and payload must be separate absolute directories",
        ));
    }
    validate_directory(
        data.parent()
            .ok_or_else(|| invalid("missing data parent"))?,
    )?;
    match fs::create_dir(data) {
        Ok(()) => (),
        Err(error) if error.kind() == io::ErrorKind::AlreadyExists => (),
        Err(error) => return Err(error),
    }
    validate_directory(data)?;
    let template = fs::read(app.join("config.env.example"))?;
    let config = data.join(".env");
    match OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&config)
    {
        Ok(mut file) => {
            file.write_all(&template)?;
            file.sync_all()?;
        }
        Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {
            if !plain(&config)?.is_file() {
                return Err(invalid("existing configuration must be a plain file"));
            }
        }
        Err(error) => return Err(error),
    }
    Ok(())
}

impl Launch {
    pub fn installed(app: &Path, data: &Path, smoke: bool) -> io::Result<Self> {
        Self::installed_source(app, data, smoke, None)
    }

    pub fn installed_source(
        app: &Path,
        data: &Path,
        smoke: bool,
        source: Option<&crate::source::SelectedSource>,
    ) -> io::Result<Self> {
        let selected = if smoke { None } else { source };
        let selected = selected
            .map(|s| crate::source::SelectedSource::validate(s.path()))
            .transpose()?;
        payload(app)?;
        validate_directory(data)?;
        if data.starts_with(app) || app.starts_with(data) {
            return Err(invalid("data and payload must be separate directories"));
        }
        let mut launch = Self::for_program(app, app.join("python").join("python.exe"), smoke, "");
        launch.env.extend([
            ("OVERSIGHT_APP_ROOT", display(app)?),
            ("OVERSIGHT_ENV_FILE", display(&data.join(".env"))?),
            (
                "OVERSIGHT_RUNTIME_DIR",
                display(&crate::source::runtime_dir(data, selected.as_ref()))?,
            ),
            ("OVERSIGHT_LEDGER_DIR", display(&data.join(".runtime"))?),
            (
                "OVERSIGHT_WORKSPACE_SOURCE",
                selected
                    .as_ref()
                    .map(|s| display(s.path()))
                    .transpose()?
                    .unwrap_or_default(),
            ),
            ("OVERSIGHT_DISCARD_OWN_RUNTIME", String::new()),
            ("PYTHONNOUSERSITE", "1".into()),
            ("PYTHONDONTWRITEBYTECODE", "1".into()),
        ]);
        launch.args.extend([
            "--demo-python".into(),
            app.join("demo-python").join("python.exe").into_os_string(),
        ]);
        Ok(launch)
    }
}

fn display(path: &Path) -> io::Result<String> {
    path.to_str()
        .map(str::to_owned)
        .ok_or_else(|| invalid("payload paths must be Unicode"))
}

/// Executable-relative resolution, independent of launch cwd; no search/fallback.
pub fn app_directory(executable: &Path) -> io::Result<PathBuf> {
    let install = executable
        .parent()
        .ok_or_else(|| invalid("missing installation directory"))?;
    validate_directory(install)?;
    Ok(install.join("app"))
}
