use std::io::{self, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::time::{Duration, Instant};

pub const MAX_RESPONSE: usize = 16 * 1024;

/// Accept only the backend's bounded, uncompressed HTTP/1 response. No redirect,
/// chunking or ambiguous length can establish live readiness.
pub fn healthy(response: &[u8]) -> bool {
    if response.len() > MAX_RESPONSE {
        return false;
    }
    let Some(split) = response.windows(4).position(|part| part == b"\r\n\r\n") else {
        return false;
    };
    if split > 4096 {
        return false;
    }
    let Ok(headers) = std::str::from_utf8(&response[..split]) else {
        return false;
    };
    let mut lines = headers.split("\r\n");
    let Some(status) = lines.next() else {
        return false;
    };
    let mut status = status.splitn(3, ' ');
    if !matches!(status.next(), Some("HTTP/1.1" | "HTTP/1.0")) || status.next() != Some("200") {
        return false;
    }
    let mut length = None;
    for line in lines {
        let Some((name, value)) = line.split_once(':') else {
            return false;
        };
        if name.eq_ignore_ascii_case("transfer-encoding")
            || name.eq_ignore_ascii_case("content-encoding")
        {
            return false;
        }
        if name.eq_ignore_ascii_case("content-length") {
            if length.is_some() {
                return false;
            }
            length = value.trim().parse::<usize>().ok();
            if length.is_none() {
                return false;
            }
        }
    }
    let body = &response[split + 4..];
    if length != Some(body.len()) {
        return false;
    }
    let Ok(value) = serde_json::from_slice::<serde_json::Value>(body) else {
        return false;
    };
    value.get("ok").and_then(|value| value.as_bool()) == Some(true)
        && value.get("mode").and_then(|value| value.as_str()) == Some("live")
}

fn remaining(deadline: Instant) -> io::Result<Duration> {
    let remaining = deadline.saturating_duration_since(Instant::now());
    if remaining.is_zero() {
        Err(io::Error::new(
            io::ErrorKind::TimedOut,
            "health deadline expired",
        ))
    } else {
        Ok(remaining)
    }
}

/// One bounded request, after a native ownership check. The caller must recheck
/// child liveness and listener ownership after this returns, before navigation.
/// No proxy, DNS, redirect or external address is consulted.
pub fn probe(address: SocketAddr, deadline: Instant) -> io::Result<bool> {
    if !address.ip().is_loopback() || address.port() == 0 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "health requires loopback",
        ));
    }
    let mut stream = TcpStream::connect_timeout(&address, remaining(deadline)?)?;
    let request = format!(
        "GET /health HTTP/1.1\r\nHost: {address}\r\nConnection: close\r\nAccept: application/json\r\n\r\n"
    );
    let mut pending = request.as_bytes();
    while !pending.is_empty() {
        stream.set_write_timeout(Some(remaining(deadline)?))?;
        let written = stream.write(pending)?;
        if written == 0 {
            return Err(io::Error::from(io::ErrorKind::WriteZero));
        }
        pending = &pending[written..];
    }
    let mut response = Vec::new();
    let mut chunk = [0; 1024];
    loop {
        stream.set_read_timeout(Some(remaining(deadline)?))?;
        let read = stream.read(&mut chunk)?;
        if read == 0 {
            remaining(deadline)?;
            return Ok(healthy(&response));
        }
        if response.len() + read > MAX_RESPONSE {
            return Ok(false);
        }
        response.extend_from_slice(&chunk[..read]);
    }
}
