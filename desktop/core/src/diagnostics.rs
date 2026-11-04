//! Closed-vocabulary startup diagnostics. Never persist error messages or environment values.
use std::collections::VecDeque;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

use crate::launch::{plain, validate_directory};

#[derive(Clone, Copy, Debug)]
pub enum Stage {
    Arguments,
    Desktop,
    Git,
    Console,
    Executable,
    Payload,
    Configuration,
    Launch,
    Listener,
    Port,
    Spawn,
    Readiness,
    Navigation,
    Running,
    Cleanup,
    Complete,
}

/// At most sixteen sanitized lines; even a backend's unterminated line is bounded.
pub struct OutputTail {
    refusal: Option<crate::refusal::Refusal>,
    pending: Vec<u8>,
    overflow: bool,
    lines: VecDeque<String>,
    app: PathBuf,
    data: PathBuf,
}

impl OutputTail {
    pub fn new(app: PathBuf, data: PathBuf) -> Self {
        Self {
            refusal: None,
            pending: Vec::new(),
            overflow: false,
            lines: VecDeque::new(),
            app,
            data,
        }
    }

    pub fn feed(&mut self, bytes: &[u8]) {
        for byte in bytes {
            if *byte == b'\n' {
                if !self.overflow {
                    let line = String::from_utf8_lossy(&self.pending);
                    if let Some(refusal) = crate::refusal::Refusal::parse(&line) {
                        self.refusal = Some(refusal);
                    }
                    if let Some(safe) = self.sanitize(line.trim()) {
                        if self.lines.len() == 16 {
                            self.lines.pop_front();
                        }
                        self.lines.push_back(safe);
                    }
                }
                self.pending.clear();
                self.overflow = false;
            } else if self.pending.len() < 1024 {
                self.pending.push(*byte);
            } else {
                self.overflow = true;
            }
        }
    }

    fn sanitize(&self, line: &str) -> Option<String> {
        for class in [
            "ModuleNotFoundError",
            "ImportError",
            "ValueError",
            "RuntimeError",
            "OSError",
            "PermissionError",
            "FileNotFoundError",
            "WorkspaceError",
            "WorkspaceBusy",
            "TypeError",
            "AttributeError",
            "SystemError",
            "SyntaxError",
            "UnicodeDecodeError",
        ] {
            if line.starts_with(&format!("{class}:")) {
                return Some(format!("backend: {class}"));
            }
        }
        if line.starts_with("Fatal Python error:") {
            return Some("backend: fatal Python error".into());
        }
        if line == "ERROR:    Application startup failed. Exiting." {
            return Some("backend: application startup failed".into());
        }
        let frame = line.strip_prefix("File \"")?;
        let (file, rest) = frame.split_once("\", line ")?;
        let number = rest.split(',').next()?;
        if number.is_empty() || number.len() > 6 || !number.bytes().all(|b| b.is_ascii_digit()) {
            return None;
        }
        // Only identify a known payload module, never source lines or arbitrary filenames.
        for root in [&self.app, &self.data] {
            if let Ok(relative) = Path::new(file).strip_prefix(root) {
                let relative = relative.to_str()?.replace('\\', "/");
                for module in [
                    "__main__",
                    "app",
                    "settings",
                    "workspace/core",
                    "workspace/_platform",
                ] {
                    if relative == format!("python/Lib/site-packages/oversight/{module}.py") {
                        return Some(format!("backend: oversight/{module}.py line {number}"));
                    }
                }
            }
        }
        None
    }

    pub fn refusal(&self) -> Option<crate::refusal::Refusal> {
        self.refusal
    }

    pub fn text(&self) -> String {
        self.lines.iter().cloned().collect::<Vec<_>>().join("\n")
    }
}

pub struct SessionLog {
    file: File,
    stage: Stage,
}

impl SessionLog {
    pub fn open(data: &Path) -> io::Result<Self> {
        validate_directory(
            data.parent()
                .ok_or_else(|| io::Error::other("missing data parent"))?,
        )?;
        for directory in [data.to_path_buf(), data.join(".runtime")] {
            match fs::create_dir(&directory) {
                Ok(()) => (),
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => (),
                Err(error) => return Err(error),
            }
            validate_directory(&directory)?;
        }
        let path = data.join(".runtime").join("desktop-session.log");
        match plain(&path) {
            Ok(metadata) if metadata.is_file() => fs::remove_file(&path)?,
            Ok(_) => return Err(io::Error::other("session log must be a plain file")),
            Err(error) if error.kind() == io::ErrorKind::NotFound => (),
            Err(error) => return Err(error),
        }
        let file = OpenOptions::new().write(true).create_new(true).open(path)?;
        let mut log = Self {
            file,
            stage: Stage::Arguments,
        };
        log.advance(Stage::Arguments)?;
        Ok(log)
    }

    pub fn advance(&mut self, stage: Stage) -> io::Result<()> {
        self.stage = stage;
        self.write(&format!("Oversight desktop session\nStage: {stage:?}\n"))
    }

    pub fn failure(
        &mut self,
        error: &io::Error,
        exit: Option<u32>,
        tail: &OutputTail,
    ) -> io::Result<()> {
        let hint = match self.stage {
            Stage::Git => "Hint: Git for Windows (git.exe) is required on PATH.\n",
            _ => "",
        };
        self.write(&format!(
            "Oversight desktop launch failed\nStage: {:?}\nKind: {:?}\n{hint}OS code: {:?}\nBackend exit: {exit:?}\n{}\n",
            self.stage, error.kind(), error.raw_os_error(), tail.text()
        ))
    }

    fn write(&mut self, text: &str) -> io::Result<()> {
        self.file.seek(SeekFrom::Start(0))?;
        self.file.set_len(0)?;
        self.file.write_all(text.as_bytes())?;
        self.file.flush()
    }
}

/// Keep cleanup evidence separate from the error that requested shutdown.
#[derive(Debug)]
pub struct CloseOutcome {
    pub primary: io::Result<()>,
    /// None means no child existed, so no shutdown failure message is warranted.
    pub cleanup: Option<io::Result<bool>>,
    pub reaped: bool,
}

impl CloseOutcome {
    pub fn unverified(&self) -> bool {
        self.cleanup
            .as_ref()
            .is_some_and(|result| !matches!(result, Ok(true)) || !self.reaped)
    }

    pub fn finish(self) -> io::Result<()> {
        if self.unverified() {
            // Preserve both the triggering error and cleanup outcome on failed shutdown.
            Err(io::Error::other(self))
        } else {
            self.primary
        }
    }
}

impl std::fmt::Display for CloseOutcome {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "session: {:?}; cleanup: {:?}; reaped: {}",
            self.primary, self.cleanup, self.reaped
        )
    }
}
impl std::error::Error for CloseOutcome {}
