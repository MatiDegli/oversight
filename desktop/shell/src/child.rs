//! Only owned handles and a console group; no shell or breakaway.
use std::ffi::{OsStr, OsString};
use std::io;
use std::mem::{size_of, zeroed};
use std::os::windows::ffi::OsStrExt;
use std::path::Path;
use std::ptr::{null, null_mut};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use oversight_desktop_core::diagnostics::OutputTail;
use oversight_desktop_core::launch::Launch;
use oversight_desktop_core::lifecycle::{Cleanup, CleanupAction};
use windows_sys::Win32::Foundation::{CloseHandle, HANDLE, WAIT_OBJECT_0, WAIT_TIMEOUT};
use windows_sys::Win32::System::Console::{CTRL_BREAK_EVENT, GenerateConsoleCtrlEvent};
use windows_sys::Win32::System::JobObjects::*;
use windows_sys::Win32::System::Threading::*;

pub struct Handle(pub HANDLE);
impl Drop for Handle {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe { CloseHandle(self.0) };
        }
    }
}

pub fn wide(value: &OsStr) -> Vec<u16> {
    value.encode_wide().chain(Some(0)).collect()
}

// Windows CommandLineToArgvW quoting, including embedded quotes/trailing slashes.
fn quote(value: &OsStr) -> Vec<u16> {
    let mut result = vec![b'"' as u16];
    let mut slashes = 0;
    for ch in value.encode_wide() {
        if ch == b'\\' as u16 {
            slashes += 1;
            continue;
        }
        result.extend(std::iter::repeat_n(
            b'\\' as u16,
            slashes * if ch == 34 { 2 } else { 1 },
        ));
        if ch == 34 {
            result.push(b'\\' as u16);
        }
        result.push(ch);
        slashes = 0;
    }
    result.extend(std::iter::repeat_n(b'\\' as u16, slashes * 2));
    result.push(34);
    result
}

fn environment(launch: &Launch) -> Vec<u16> {
    let mut vars = std::collections::BTreeMap::new();
    for (key, value) in std::env::vars_os() {
        let upper = key.to_string_lossy().to_ascii_uppercase();
        if upper.starts_with("GIT_")
            || upper.starts_with("PYTHON")
            || launch.remove_env.iter().any(|name| upper == *name)
            || launch.env.keys().any(|name| upper == *name)
        {
            continue;
        }
        vars.insert(upper, (key, value));
    }
    for (key, value) in &launch.env {
        vars.insert(
            key.to_string(),
            (OsString::from(key), OsString::from(value)),
        );
    }
    let mut result = Vec::new();
    for (_, (key, value)) in vars {
        result.extend(wide(&key).into_iter().take_while(|v| *v != 0));
        result.push(61);
        result.extend(wide(&value));
    }
    result.push(0);
    result
}

pub struct Child {
    pub process: Handle,
    pub job: Handle,
    pub pid: u32,
    output: std::thread::JoinHandle<()>,
}

impl Child {
    pub fn spawn(launch: &Launch, tail: Arc<Mutex<OutputTail>>) -> io::Result<Self> {
        unsafe {
            let job = Handle(CreateJobObjectW(null(), null()));
            if job.0.is_null() {
                return Err(io::Error::last_os_error());
            }
            let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = zeroed();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if SetInformationJobObject(
                job.0,
                JobObjectExtendedLimitInformation,
                &limits as *const _ as _,
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            ) == 0
            {
                return Err(io::Error::last_os_error());
            }
            let mut command = quote(launch.program.as_os_str());
            for arg in &launch.args {
                command.push(32);
                command.extend(quote(arg));
            }
            command.push(0);
            let env = environment(launch);
            let mut redirect = crate::output::Redirect::new(tail)?;
            let mut info: PROCESS_INFORMATION = zeroed();
            if CreateProcessW(
                wide(launch.program.as_os_str()).as_ptr(),
                command.as_mut_ptr(),
                null(),
                null(),
                1,
                CREATE_SUSPENDED
                    | CREATE_NEW_PROCESS_GROUP
                    | CREATE_UNICODE_ENVIRONMENT
                    | EXTENDED_STARTUPINFO_PRESENT,
                env.as_ptr() as _,
                wide(launch.cwd.as_os_str()).as_ptr(),
                &redirect.startup.StartupInfo,
                &mut info,
            ) == 0
            {
                return Err(io::Error::last_os_error());
            }
            let process = Handle(info.hProcess);
            let thread = Handle(info.hThread);
            if AssignProcessToJobObject(job.0, process.0) == 0 || ResumeThread(thread.0) == u32::MAX
            {
                let error = io::Error::last_os_error();
                let terminated = TerminateProcess(process.0, 1) != 0;
                let reaped = WaitForSingleObject(process.0, 5000) == WAIT_OBJECT_0;
                if !terminated || !reaped {
                    return Err(io::Error::other("suspended startup child cleanup failed"));
                }
                return Err(error);
            }
            Ok(Self {
                process,
                job,
                pid: info.dwProcessId,
                output: redirect.reader.take().unwrap(),
            })
        }
    }

    pub fn alive(&self) -> bool {
        unsafe { WaitForSingleObject(self.process.0, 0) == WAIT_TIMEOUT }
    }

    pub fn exit_code(&self) -> Option<u32> {
        let mut code = 0;
        if self.alive() || unsafe { GetExitCodeProcess(self.process.0, &mut code) } == 0 {
            None
        } else {
            Some(code)
        }
    }

    pub fn drain_output(&self) {
        // Cleanup has reaped the child. Bound the final pipe drain even if a writer misbehaves.
        let deadline = Instant::now() + Duration::from_millis(250);
        while !self.output.is_finished() && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(5));
        }
    }

    fn active(&self) -> io::Result<bool> {
        unsafe {
            let mut info: JOBOBJECT_BASIC_ACCOUNTING_INFORMATION = zeroed();
            if QueryInformationJobObject(
                self.job.0,
                JobObjectBasicAccountingInformation,
                &mut info as *mut _ as _,
                size_of::<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION>() as u32,
                null_mut(),
            ) == 0
            {
                return Err(io::Error::last_os_error());
            }
            Ok(info.ActiveProcesses != 0)
        }
    }

    pub fn close(&self) -> io::Result<bool> {
        let began = Instant::now();
        let mut policy = Cleanup::default();
        loop {
            match policy.poll(began.elapsed(), self.active()?) {
                CleanupAction::SignalOwnedGroup => unsafe {
                    if self.alive() && GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, self.pid) == 0 {
                        return Err(io::Error::last_os_error());
                    }
                },
                CleanupAction::Wait => std::thread::sleep(Duration::from_millis(25)),
                CleanupAction::TerminateOwnedJob => unsafe {
                    if TerminateJobObject(self.job.0, 1) == 0 {
                        return Err(io::Error::last_os_error());
                    }
                },
                CleanupAction::ReapAndClose => {
                    if unsafe { WaitForSingleObject(self.process.0, 1000) } != WAIT_OBJECT_0 {
                        return Err(io::Error::other("owned child did not reap"));
                    }
                    return Ok(policy.graceful());
                }
                CleanupAction::Done => return Ok(policy.graceful()),
            }
            if began.elapsed() > Duration::from_secs(10) {
                return Err(io::Error::other("owned Job cleanup could not be verified"));
            }
        }
    }
}

pub fn require_git() -> io::Result<()> {
    let path = std::env::var_os("PATH").unwrap_or_default();
    for directory in std::env::split_paths(&path) {
        if directory.is_absolute() && Path::new(&directory).join("git.exe").is_file() {
            return Ok(());
        }
    }
    Err(io::Error::new(
        io::ErrorKind::NotFound,
        "Git for Windows (git.exe) is required on PATH",
    ))
}
