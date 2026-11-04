//! Native menu selections and deterministic state placement. No source Git is run here.
use std::io;
use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};

use crate::launch::{invalid, native_drive_path};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SelectedSource(PathBuf);

impl SelectedSource {
    /// Lexical admission before any filesystem access; the backend remains authoritative
    /// for standalone Git, placement, links, inventory and prepared environment checks.
    pub fn validate(path: &Path) -> io::Result<Self> {
        if !path.to_str().is_some_and(native_drive_path) {
            return Err(invalid("select an absolute local Windows repository path"));
        }
        Ok(Self(path.to_path_buf()))
    }

    pub fn path(&self) -> &Path {
        &self.0
    }

    pub fn runtime_dir(&self, data: &Path) -> PathBuf {
        // Windows normcase: separator spelling is already fixed by validation.
        let normalized = self.0.to_str().unwrap().to_lowercase();
        let digest = format!("{:x}", Sha256::digest(normalized.as_bytes()));
        data.join("sources").join(&digest[..16]).join(".runtime")
    }
}

pub fn runtime_dir(data: &Path, source: Option<&SelectedSource>) -> PathBuf {
    source.map_or_else(|| data.join(".runtime"), |source| source.runtime_dir(data))
}
