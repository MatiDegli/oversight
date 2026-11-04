//! Inherit only the explicit stdout/stderr pipe and NUL stdin, never other handles.
use std::fs::File;
use std::io::{self, Read};
use std::mem::{size_of, zeroed};
use std::os::windows::io::{AsRawHandle, FromRawHandle};
use std::ptr::{null, null_mut};
use std::sync::{Arc, Mutex};

use oversight_desktop_core::diagnostics::OutputTail;
use windows_sys::Win32::Foundation::{HANDLE, HANDLE_FLAG_INHERIT, SetHandleInformation};
use windows_sys::Win32::Security::SECURITY_ATTRIBUTES;
use windows_sys::Win32::System::Pipes::CreatePipe;
use windows_sys::Win32::System::Threading::*;

use crate::child::Handle;

pub struct Redirect {
    pub startup: STARTUPINFOEXW,
    _attributes: Vec<usize>,
    _handles: Box<[HANDLE; 2]>,
    _writer: Handle,
    _input: File,
    pub reader: Option<std::thread::JoinHandle<()>>,
}

impl Redirect {
    pub fn new(tail: Arc<Mutex<OutputTail>>) -> io::Result<Self> {
        unsafe {
            let security = SECURITY_ATTRIBUTES {
                nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
                lpSecurityDescriptor: null_mut(),
                bInheritHandle: 1,
            };
            let (mut read, mut write) = (null_mut(), null_mut());
            if CreatePipe(&mut read, &mut write, &security, 0) == 0 {
                return Err(io::Error::last_os_error());
            }
            let mut reader = File::from_raw_handle(read);
            let writer = Handle(write);
            if SetHandleInformation(read, HANDLE_FLAG_INHERIT, 0) == 0 {
                return Err(io::Error::last_os_error());
            }
            let input = File::open("NUL")?;
            if SetHandleInformation(
                input.as_raw_handle(),
                HANDLE_FLAG_INHERIT,
                HANDLE_FLAG_INHERIT,
            ) == 0
            {
                return Err(io::Error::last_os_error());
            }
            let mut bytes = 0;
            InitializeProcThreadAttributeList(null_mut(), 1, 0, &mut bytes);
            let mut attributes = vec![0usize; bytes.div_ceil(size_of::<usize>())];
            let mut startup: STARTUPINFOEXW = zeroed();
            startup.StartupInfo.cb = size_of::<STARTUPINFOEXW>() as u32;
            startup.lpAttributeList = attributes.as_mut_ptr() as _;
            if InitializeProcThreadAttributeList(startup.lpAttributeList, 1, 0, &mut bytes) == 0 {
                return Err(io::Error::last_os_error());
            }
            let handles = Box::new([write, input.as_raw_handle()]);
            let mut redirect = Self {
                startup,
                _attributes: attributes,
                _handles: handles,
                _writer: writer,
                _input: input,
                reader: None,
            };
            if UpdateProcThreadAttribute(
                redirect.startup.lpAttributeList,
                0,
                PROC_THREAD_ATTRIBUTE_HANDLE_LIST as usize,
                redirect._handles.as_mut_ptr() as _,
                size_of::<[HANDLE; 2]>(),
                null_mut(),
                null(),
            ) == 0
            {
                return Err(io::Error::last_os_error());
            }
            redirect.startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
            redirect.startup.StartupInfo.hStdInput = redirect._input.as_raw_handle();
            redirect.startup.StartupInfo.hStdOutput = write;
            redirect.startup.StartupInfo.hStdError = write;
            redirect.reader = Some(
                std::thread::Builder::new()
                    .name("backend-output".into())
                    .spawn(move || {
                        let mut buffer = [0u8; 2048];
                        while let Ok(count) = reader.read(&mut buffer) {
                            if count == 0 {
                                break;
                            }
                            if let Ok(mut tail) = tail.lock() {
                                tail.feed(&buffer[..count]);
                            }
                        }
                        if let Ok(mut tail) = tail.lock() {
                            tail.feed(b"\n");
                        }
                    })?,
            );
            Ok(redirect)
        }
    }
}

impl Drop for Redirect {
    fn drop(&mut self) {
        unsafe {
            DeleteProcThreadAttributeList(self.startup.lpAttributeList);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use std::os::windows::io::BorrowedHandle;
    use windows_sys::Win32::Foundation::GetHandleInformation;

    #[test]
    fn explicit_stdio_handles_feed_only_sanitized_output() {
        let tail = Arc::new(Mutex::new(OutputTail::new(
            "C:\\install\\app".into(),
            "C:\\data".into(),
        )));
        let mut redirect = Redirect::new(tail.clone()).unwrap();
        assert_eq!(
            redirect.startup.StartupInfo.hStdOutput,
            redirect.startup.StartupInfo.hStdError
        );
        for handle in *redirect._handles {
            let mut flags = 0;
            assert_ne!(unsafe { GetHandleInformation(handle, &mut flags) }, 0);
            assert_ne!(flags & HANDLE_FLAG_INHERIT, 0);
        }
        {
            let owned =
                unsafe { BorrowedHandle::borrow_raw(redirect.startup.StartupInfo.hStdError) }
                    .try_clone_to_owned()
                    .unwrap();
            File::from(owned)
                .write_all(b"ValueError: secret\nENV=secret\n")
                .unwrap();
        }
        let reader = redirect.reader.take().unwrap();
        drop(redirect);
        reader.join().unwrap();
        assert_eq!(tail.lock().unwrap().text(), "backend: ValueError");
    }
}
