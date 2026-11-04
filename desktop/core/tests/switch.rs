use std::path::Path;
use std::time::Duration;

use oversight_desktop_core::source::SelectedSource;
use oversight_desktop_core::switch::{Action, PAGE_LIMIT, Phase, Switch};

#[test]
fn one_switch_until_ready_or_refused_and_no_selection_on_cancel() {
    let mut state = Switch::default();
    assert!(!state.can_open());
    assert_eq!(state.confirm(None, Duration::ZERO), Action::None);
    state.startup(true);
    assert!(state.can_open());
    assert_eq!(state.take_source(), None); // Cancel sends nothing.
    let source = SelectedSource::validate(Path::new(r"C:\private\repo")).unwrap();
    assert_eq!(
        state.confirm(Some(source.clone()), Duration::ZERO),
        Action::LoadPage
    );
    assert!(!state.can_open());
    assert_eq!(state.confirm(None, Duration::ZERO), Action::None);
    assert_eq!(state.take_source(), None);
    assert_eq!(state.closed(true, true), Action::None);
    assert_eq!(state.page(Duration::from_secs(1), true), Action::Close);
    assert_eq!(state.closed(true, true), Action::Launch);
    assert!(!state.can_open());
    assert_eq!(state.take_source(), Some(Some(source)));
    assert_eq!(state.take_source(), None);
    state.startup(false);
    assert!(state.can_open());
    assert_eq!(state.phase(), Phase::Refused);
    assert_eq!(state.confirm(None, Duration::ZERO), Action::LoadPage);
    assert_eq!(state.page(Duration::from_secs(1), true), Action::Close);
    assert_eq!(state.closed(true, true), Action::Launch);
    assert_eq!(state.take_source(), Some(None));
    state.startup(true);
    assert!(state.can_open());
}

#[test]
fn page_deadline_stops_nothing_and_discards_request() {
    for ready in [true, false] {
        let mut state = Switch::default();
        state.startup(ready);
        let previous = state.phase();
        state.confirm(None, Duration::ZERO);
        assert_eq!(
            state.page(PAGE_LIMIT - Duration::from_millis(1), false),
            Action::None
        );
        assert_eq!(state.page(PAGE_LIMIT, true), Action::PageTimeout);
        assert_eq!(state.phase(), previous);
        assert!(state.can_open());
        assert_eq!(state.closed(true, true), Action::None);
        assert_eq!(state.page(PAGE_LIMIT, true), Action::None);
        assert_eq!(state.take_source(), None);
    }
}

#[test]
fn relaunch_requires_graceful_close_and_reap() {
    for (graceful, reaped) in [(false, false), (false, true), (true, false)] {
        let mut state = Switch::default();
        state.startup(true);
        state.confirm(None, Duration::ZERO);
        state.page(Duration::ZERO, true);
        assert_eq!(state.closed(graceful, reaped), Action::StopFailed);
        assert_eq!(state.phase(), Phase::Stopped);
        assert!(!state.can_open());
        assert_eq!(state.take_source(), None);
    }
}

#[test]
fn quit_is_sticky_and_cancels_every_pending_phase_including_readiness() {
    for phase in 0..5 {
        let mut state = Switch::default();
        if phase > 0 {
            state.startup(true);
        }
        if phase > 1 {
            state.confirm(None, Duration::ZERO);
        }
        if phase > 2 {
            state.page(Duration::ZERO, true);
        }
        if phase > 3 {
            state.closed(true, true);
        }
        assert_eq!(state.quit(), Action::Close);
        assert!(!state.can_open());
        assert_eq!(state.confirm(None, Duration::ZERO), Action::None);
        assert_eq!(state.take_source(), None);
        assert_eq!(state.startup(true), Action::Close);
        assert_eq!(state.closed(true, true), Action::Exit);
        assert_eq!(state.quit(), Action::Exit);
        assert_eq!(state.take_source(), None);
    }
}
