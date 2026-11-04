use std::io;
use std::mem::{size_of, zeroed};
use std::ptr::null_mut;

use oversight_desktop_core::PORT;
use oversight_desktop_core::lifecycle::Listener;
use windows_sys::Win32::Foundation::{ERROR_INSUFFICIENT_BUFFER, NO_ERROR};
use windows_sys::Win32::NetworkManagement::IpHelper::*;
use windows_sys::Win32::Networking::WinSock::{AF_INET, AF_INET6};
use windows_sys::Win32::System::JobObjects::IsProcessInJob;
use windows_sys::Win32::System::Threading::{OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION};

use crate::child::{Child, Handle};

fn table(family: u32) -> io::Result<(Vec<u64>, u32)> {
    unsafe {
        let mut bytes = 0;
        let mut buffer = Vec::<u64>::new();
        for _ in 0..4 {
            let pointer = if buffer.is_empty() {
                null_mut()
            } else {
                buffer.as_mut_ptr() as _
            };
            let status = GetExtendedTcpTable(
                pointer,
                &mut bytes,
                0,
                family,
                TCP_TABLE_OWNER_PID_LISTENER,
                0,
            );
            if status == NO_ERROR {
                if buffer.is_empty() || bytes < 4 || bytes as usize > buffer.len() * 8 {
                    return Err(io::Error::other("invalid listener buffer"));
                }
                return Ok((buffer, bytes));
            }
            if status != ERROR_INSUFFICIENT_BUFFER {
                return Err(io::Error::from_raw_os_error(status as i32));
            }
            buffer.resize((bytes as usize).div_ceil(8), 0);
        }
        Err(io::Error::other("listener table changed repeatedly"))
    }
}

fn owners(family: u32) -> io::Result<Vec<(u32, bool)>> {
    let (buffer, bytes) = table(family)?;
    unsafe {
        let count = *(buffer.as_ptr() as *const u32) as usize;
        let row_size = if family == AF_INET as u32 {
            size_of::<MIB_TCPROW_OWNER_PID>()
        } else {
            size_of::<MIB_TCP6ROW_OWNER_PID>()
        };
        if 4 + count * row_size > bytes as usize {
            return Err(io::Error::other("invalid listener table"));
        }
        let mut result = Vec::new();
        if family == AF_INET as u32 {
            let table = &*(buffer.as_ptr() as *const MIB_TCPTABLE_OWNER_PID);
            for row in std::slice::from_raw_parts(table.table.as_ptr(), count) {
                if u16::from_be(row.dwLocalPort as u16) == PORT {
                    result.push((
                        row.dwOwningPid,
                        row.dwLocalAddr == u32::from_ne_bytes([127, 0, 0, 1]),
                    ));
                }
            }
        } else {
            let table = &*(buffer.as_ptr() as *const MIB_TCP6TABLE_OWNER_PID);
            for row in std::slice::from_raw_parts(table.table.as_ptr(), count) {
                if u16::from_be(row.dwLocalPort as u16) == PORT {
                    result.push((row.dwOwningPid, false));
                }
            }
        }
        Ok(result)
    }
}

pub fn require_unoccupied() -> io::Result<()> {
    if !owners(AF_INET as u32)?.is_empty() || !owners(AF_INET6 as u32)?.is_empty() {
        return Err(io::Error::new(
            io::ErrorKind::AddrInUse,
            "port 8710 is occupied",
        ));
    }
    Ok(())
}

pub fn observe(child: &Child) -> io::Result<Listener> {
    let rows = owners(AF_INET as u32)?
        .into_iter()
        .chain(owners(AF_INET6 as u32)?);
    let mut found = Listener::Absent;
    for (pid, loopback) in rows {
        unsafe {
            let process = Handle(OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid));
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
            if loopback {
                found = Listener::Owned;
            }
        }
    }
    Ok(found)
}
