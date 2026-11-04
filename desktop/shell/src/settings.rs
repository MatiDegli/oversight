//! Open the settings path in the system's Notepad; never inspect its contents.
use std::ffi::OsString;
use std::io;
use std::os::windows::ffi::OsStringExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use oversight_desktop_core::installed::{initialize_data, notepad_path, settings_file};
use windows_sys::Win32::System::SystemInformation::GetSystemDirectoryW;

fn system_directory() -> io::Result<PathBuf> {
    let mut buffer = vec![0u16; 32768];
    let length = unsafe { GetSystemDirectoryW(buffer.as_mut_ptr(), buffer.len() as u32) };
    if length == 0 {
        return Err(io::Error::last_os_error());
    }
    if length as usize >= buffer.len() {
        return Err(io::Error::other("system directory exceeds path limit"));
    }
    Ok(PathBuf::from(OsString::from_wide(
        &buffer[..length as usize],
    )))
}

pub fn edit(app: &Path, data: &Path) -> io::Result<()> {
    initialize_data(app, data)?;
    let program = notepad_path(&system_directory()?)?;
    Command::new(program)
        .arg(settings_file(data))
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn native_system_directory_supplies_an_absolute_notepad_path() {
        let system = system_directory().unwrap();
        assert!(system.is_absolute());
        assert_eq!(notepad_path(&system).unwrap(), system.join("notepad.exe"));
    }
}
