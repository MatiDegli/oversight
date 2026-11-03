//! Portable launch and supervision policy for a future native Windows shell.
//!
//! This crate never starts a process or claims listener ownership. The platform
//! adapter must establish ownership before asking readiness to admit a window.

pub mod health;
pub mod launch;
pub mod lifecycle;

pub const HOST: &str = "127.0.0.1";
pub const PORT: u16 = 8710;
pub const URL: &str = "http://127.0.0.1:8710/";
pub const STARTUP_LIMIT: std::time::Duration = std::time::Duration::from_secs(120);
pub const CLOSE_LIMIT: std::time::Duration = std::time::Duration::from_secs(5);
