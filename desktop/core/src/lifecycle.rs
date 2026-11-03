use std::time::Duration;

use crate::{CLOSE_LIMIT, STARTUP_LIMIT};

#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum Listener {
    Absent,
    Owned,
    Foreign,
    Unknown,
}

#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum Failure {
    Deadline,
    ChildExited,
    ForeignListener,
}

#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum Readiness {
    Waiting,
    Ready,
    Failed(Failure),
}

#[derive(Debug)]
pub struct Startup {
    deadline: Duration,
    state: Readiness,
}

impl Startup {
    /// `now` is elapsed monotonic time from the adapter, never wall-clock time.
    pub fn new(now: Duration) -> Self {
        Self {
            deadline: now.saturating_add(STARTUP_LIMIT),
            state: Readiness::Waiting,
        }
    }

    pub fn observe(
        &mut self,
        now: Duration,
        child_alive: bool,
        listener: Listener,
        health_ok: bool,
    ) -> Readiness {
        // Failure is sticky. A late successful HTTP response cannot revive it.
        if matches!(self.state, Readiness::Failed(_)) {
            return self.state;
        }
        self.state = if !child_alive {
            Readiness::Failed(Failure::ChildExited)
        } else if listener == Listener::Foreign {
            Readiness::Failed(Failure::ForeignListener)
        } else if self.state == Readiness::Ready {
            // Continue checking liveness/ownership after the window is admitted.
            if listener == Listener::Owned {
                Readiness::Ready
            } else {
                Readiness::Failed(Failure::ForeignListener)
            }
        } else if now >= self.deadline {
            Readiness::Failed(Failure::Deadline)
        } else if listener == Listener::Owned && health_ok {
            Readiness::Ready
        } else {
            Readiness::Waiting
        };
        self.state
    }

    pub fn remaining(&self, now: Duration) -> Duration {
        self.deadline.saturating_sub(now)
    }
}

#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum CleanupAction {
    SignalOwnedGroup,
    Wait,
    TerminateOwnedJob,
    ReapAndClose,
    Done,
}

#[derive(Debug, Default)]
pub struct Cleanup {
    started: Option<Duration>,
    forced: bool,
    closed: bool,
}

impl Cleanup {
    /// Used for close, failed startup and app exit. The adapter executes each
    /// action before polling again; it must retain handles until ReapAndClose.
    pub fn poll(&mut self, now: Duration, owned_processes_alive: bool) -> CleanupAction {
        if self.closed {
            return CleanupAction::Done;
        }
        if !owned_processes_alive {
            self.closed = true;
            return CleanupAction::ReapAndClose;
        }
        let Some(started) = self.started else {
            self.started = Some(now);
            return CleanupAction::SignalOwnedGroup;
        };
        if now.saturating_sub(started) < CLOSE_LIMIT || self.forced {
            return CleanupAction::Wait;
        }
        self.forced = true;
        CleanupAction::TerminateOwnedJob
    }

    /// Only meaningful after handles have been reaped/closed. Forced termination
    /// never supplies graceful acceptance, even if it leaves no survivors.
    pub fn graceful(&self) -> bool {
        self.closed && !self.forced
    }
}
