use std::time::Duration;

use oversight_desktop_core::lifecycle::{
    Cleanup, CleanupAction as Action, Failure, Listener, Readiness, Startup,
};

fn second(value: u64) -> Duration {
    Duration::from_secs(value)
}

#[test]
fn readiness_needs_owned_listener_live_child_and_valid_health() {
    for listener in [Listener::Absent, Listener::Unknown] {
        let mut startup = Startup::new(second(0));
        assert_eq!(
            startup.observe(second(1), true, listener, true),
            Readiness::Waiting
        );
    }
    let mut startup = Startup::new(second(0));
    assert_eq!(
        startup.observe(second(1), true, Listener::Owned, false),
        Readiness::Waiting
    );
    assert_eq!(
        startup.observe(second(119), true, Listener::Owned, true),
        Readiness::Ready
    );
    assert_eq!(
        startup.observe(second(200), true, Listener::Owned, false),
        Readiness::Ready
    );
}

#[test]
fn deadline_is_absolute_inclusive_and_late_health_cannot_revive_failure() {
    let mut startup = Startup::new(second(10));
    assert_eq!(startup.remaining(second(129)), second(1));
    assert_eq!(startup.remaining(second(131)), second(0));
    assert_eq!(
        startup.observe(second(129), true, Listener::Owned, false),
        Readiness::Waiting
    );
    assert_eq!(
        startup.observe(second(130), true, Listener::Owned, true),
        Readiness::Failed(Failure::Deadline)
    );
    assert_eq!(
        startup.observe(second(131), true, Listener::Owned, true),
        Readiness::Failed(Failure::Deadline)
    );
}

#[test]
fn death_or_foreign_listener_fails_before_and_after_readiness() {
    for already_ready in [false, true] {
        for (alive, listener, failure) in [
            (false, Listener::Owned, Failure::ChildExited),
            (true, Listener::Foreign, Failure::ForeignListener),
        ] {
            let mut startup = Startup::new(second(0));
            if already_ready {
                assert_eq!(
                    startup.observe(second(1), true, Listener::Owned, true),
                    Readiness::Ready
                );
            }
            assert_eq!(
                startup.observe(second(2), alive, listener, true),
                Readiness::Failed(failure)
            );
            assert_eq!(
                startup.observe(second(3), true, Listener::Owned, true),
                Readiness::Failed(failure)
            );
        }
    }
}

#[test]
fn lost_ownership_after_readiness_fails_closed() {
    for listener in [Listener::Absent, Listener::Unknown] {
        let mut startup = Startup::new(second(0));
        startup.observe(second(1), true, Listener::Owned, true);
        assert_eq!(
            startup.observe(second(2), true, listener, true),
            Readiness::Failed(Failure::ForeignListener)
        );
    }
}

#[test]
fn graceful_close_signals_once_waits_then_reaps_and_closes_once() {
    let mut cleanup = Cleanup::default();
    assert!(!cleanup.graceful());
    assert_eq!(cleanup.poll(second(12), true), Action::SignalOwnedGroup);
    assert_eq!(cleanup.poll(second(16), true), Action::Wait);
    assert_eq!(cleanup.poll(second(16), false), Action::ReapAndClose);
    assert!(cleanup.graceful());
    assert_eq!(cleanup.poll(second(17), false), Action::Done);
    assert_eq!(cleanup.poll(second(18), true), Action::Done);
}

#[test]
fn surviving_descendants_force_job_cleanup_even_if_parent_exited() {
    let mut cleanup = Cleanup::default();
    assert_eq!(cleanup.poll(second(0), true), Action::SignalOwnedGroup);
    assert_eq!(cleanup.poll(second(4), true), Action::Wait);
    assert_eq!(cleanup.poll(second(5), true), Action::TerminateOwnedJob);
    assert_eq!(cleanup.poll(second(6), true), Action::Wait);
    assert_eq!(cleanup.poll(second(6), false), Action::ReapAndClose);
    assert_eq!(cleanup.poll(second(7), false), Action::Done);
    assert!(!cleanup.graceful());
}

#[test]
fn close_during_startup_and_failed_start_share_idempotent_cleanup() {
    for child_was_created in [false, true] {
        let mut cleanup = Cleanup::default();
        if child_was_created {
            assert_eq!(cleanup.poll(second(1), true), Action::SignalOwnedGroup);
        }
        assert_eq!(cleanup.poll(second(2), false), Action::ReapAndClose);
        assert_eq!(cleanup.poll(second(2), false), Action::Done);
    }
}
