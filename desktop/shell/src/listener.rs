use std::io;
use std::mem::{size_of, zeroed};
use std::ptr::null_mut;

use oversight_desktop_core::PORT;
use oversight_desktop_core::lifecycle::Listener;
use windows_sys::Win32::Foundation::{ERROR_INSUFFICIENT_BUFFER, NO_ERROR};
use windows_sys::Win32::NetworkManagement::IpHelper::*;
use windows_sys::Win32::Networking::WinSock::AF_INET;
use windows_sys::Win32::System::JobObjects::IsProcessInJob;
use windows_sys::Win32::System::Threading::{OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION};

use crate::child::{Child, Handle};

pub fn observe(child: &Child) -> io::Result<Listener> {
    unsafe {
        let mut bytes = 0;
        let status = GetExtendedTcpTable(
            null_mut(),
            &mut bytes,
            0,
            AF_INET as u32,
            TCP_TABLE_OWNER_PID_LISTENER,
            0,
        );
        if status != ERROR_INSUFFICIENT_BUFFER {
            return Err(io::Error::from_raw_os_error(status as i32));
        }
        let mut buffer = vec![0_u64; (bytes as usize).div_ceil(8)];
        let status = GetExtendedTcpTable(
            buffer.as_mut_ptr() as _,
            &mut bytes,
            0,
            AF_INET as u32,
            TCP_TABLE_OWNER_PID_LISTENER,
            0,
        );
        if status != NO_ERROR {
            return Err(io::Error::from_raw_os_error(status as i32));
        }
        let table = &*(buffer.as_ptr() as *const MIB_TCPTABLE_OWNER_PID);
        let count = table.dwNumEntries as usize;
        if 4 + count * size_of::<MIB_TCPROW_OWNER_PID>() > bytes as usize {
            return Err(io::Error::other("invalid listener table"));
        }
        let rows = std::slice::from_raw_parts(table.table.as_ptr(), count);
        let mut found = Listener::Absent;
        for row in rows {
            if u16::from_be(row.dwLocalPort as u16) != PORT {
                continue;
            }
            let process = Handle(OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION,
                0,
                row.dwOwningPid,
            ));
            if process.0.is_null() {
                return Ok(Listener::Unknown);
            }
            let mut member = zeroed();
            if IsProcessInJob(process.0, child.job.0, &mut member) == 0 {
                return Ok(Listener::Unknown);
            }
            if member == 0 {
                return Ok(Listener::Foreign);
            }
            // Require the backend's exact loopback listener, not another owned interface.
            if row.dwLocalAddr == u32::from_ne_bytes([127, 0, 0, 1]) {
                found = Listener::Owned;
            }
        }
        Ok(found)
    }
}
