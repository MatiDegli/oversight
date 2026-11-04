use std::io;
use std::net::SocketAddr;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread::sleep;
use std::time::{Duration, Instant};

use oversight_desktop_core::health;
use oversight_desktop_core::lifecycle::{Failure, Readiness, Startup};

use crate::{child::Child, listener};

pub fn ready(child: &Child, stop: &AtomicBool) -> io::Result<()> {
    let began = Instant::now();
    let mut policy = Startup::new(Duration::ZERO);
    let address: SocketAddr = "127.0.0.1:8710".parse().unwrap();
    loop {
        if stop.load(Ordering::SeqCst) {
            return Err(io::Error::other("startup cancelled"));
        }
        let listener = listener::observe(child)?;
        let mut state = policy.observe(began.elapsed(), child.alive(), listener, false);
        if state == Readiness::Waiting
            && listener == oversight_desktop_core::lifecycle::Listener::Owned
        {
            let deadline = (Instant::now() + Duration::from_millis(500))
                .min(began + oversight_desktop_core::STARTUP_LIMIT);
            let healthy = health::probe(address, deadline).unwrap_or(false);
            state = policy.observe(
                began.elapsed(),
                child.alive(),
                listener::observe(child)?,
                healthy,
            );
        }
        match state {
            Readiness::Ready => {
                if stop.load(Ordering::SeqCst) {
                    return Err(io::Error::other("startup cancelled"));
                }
                return Ok(());
            }
            Readiness::Failed(reason) => {
                let kind = match reason {
                    Failure::ChildExited => io::ErrorKind::BrokenPipe,
                    Failure::ForeignListener => io::ErrorKind::AddrInUse,
                    Failure::Deadline => io::ErrorKind::TimedOut,
                };
                return Err(io::Error::new(kind, "backend startup failed"));
            }
            Readiness::Waiting => sleep(Duration::from_millis(100)),
        }
    }
}
