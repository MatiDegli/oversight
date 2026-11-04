//! Pure switching policy. Adapters supply page completion and verified cleanup evidence.
use std::time::Duration;

use crate::source::SelectedSource;

pub const PAGE_LIMIT: Duration = Duration::from_secs(5);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Phase {
    Readiness,
    Ready,
    Refused,
    LoadingPage,
    Closing,
    Stopped,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Action {
    None,
    LoadPage,
    Close,
    Launch,
    PageTimeout,
    StopFailed,
    Exit,
}

#[derive(Debug)]
pub struct Switch {
    phase: Phase,
    quit: bool,
    pending: Option<Option<SelectedSource>>,
    deadline: Duration,
    previous: Phase,
}

impl Default for Switch {
    fn default() -> Self {
        Self {
            phase: Phase::Readiness,
            quit: false,
            pending: None,
            deadline: Duration::ZERO,
            previous: Phase::Ready,
        }
    }
}

impl Switch {
    pub fn phase(&self) -> Phase {
        self.phase
    }

    pub fn can_open(&self) -> bool {
        !self.quit && matches!(self.phase, Phase::Ready | Phase::Refused)
    }

    pub fn confirm(&mut self, source: Option<SelectedSource>, now: Duration) -> Action {
        if !self.can_open() {
            return Action::None;
        }
        self.pending = Some(source);
        self.previous = self.phase;
        self.phase = Phase::LoadingPage;
        self.deadline = now.saturating_add(PAGE_LIMIT);
        Action::LoadPage
    }

    pub fn page(&mut self, now: Duration, finished: bool) -> Action {
        if self.phase != Phase::LoadingPage {
            return Action::None;
        }
        if now >= self.deadline {
            self.pending = None;
            self.phase = self.previous;
            return Action::PageTimeout;
        }
        if finished {
            self.phase = Phase::Closing;
            return Action::Close;
        }
        Action::None
    }

    /// Quit is sticky, also during page loading/readiness. Never consumes a pending source.
    pub fn quit(&mut self) -> Action {
        self.quit = true;
        self.pending = None;
        if self.phase == Phase::Stopped {
            return Action::Exit;
        }
        self.phase = Phase::Closing;
        Action::Close
    }

    /// `graceful` means close returned Ok(true); reaping is independently required.
    pub fn closed(&mut self, graceful: bool, reaped: bool) -> Action {
        if self.phase != Phase::Closing {
            return Action::None;
        }
        if !graceful || !reaped {
            self.pending = None;
            self.quit = true;
            self.phase = Phase::Stopped;
            return Action::StopFailed;
        }
        if self.quit {
            self.phase = Phase::Stopped;
            return Action::Exit;
        }
        self.phase = Phase::Readiness;
        Action::Launch
    }

    pub fn take_source(&mut self) -> Option<Option<SelectedSource>> {
        if self.phase == Phase::Readiness && !self.quit {
            self.pending.take()
        } else {
            None
        }
    }

    pub fn startup(&mut self, ready: bool) -> Action {
        if self.quit {
            return self.quit();
        }
        if self.phase == Phase::Readiness {
            self.phase = if ready { Phase::Ready } else { Phase::Refused };
        }
        Action::None
    }
}
