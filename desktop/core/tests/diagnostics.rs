use oversight_desktop_core::diagnostics::{OutputTail, SessionLog, Stage};
use std::fs;
use std::io;
use std::path::PathBuf;

#[test]
fn backend_tail_discards_free_text_secrets_paths_and_unbounded_lines() {
    let mut tail = OutputTail::new(PathBuf::from("/install/app"), PathBuf::from("/data"));
    tail.feed(b"META_API_KEY=secret\nModuleNotFoundError: secret outside /private/home\n");
    tail.feed(b"  File \"/install/app/python/Lib/site-packages/oversight/settings.py\", line 42, in secret\n");
    tail.feed(b"  File \"/private/home/secret.py\", line 12, in secret\n");
    tail.feed(b"  File \"/install/app/../secret.py\", line 12, in secret\n");
    tail.feed(b"  File \"/data/secret.py\", line 12, in secret\n");
    tail.feed(&vec![b'x'; 100_000]);
    tail.feed(b"ValueError: secret\n");
    assert_eq!(
        tail.text(),
        "backend: ModuleNotFoundError\nbackend: oversight/settings.py line 42"
    );
    for _ in 0..100 {
        tail.feed(b"RuntimeError: secret\n");
    }
    assert_eq!(tail.text().lines().count(), 16);
    assert!(!tail.text().contains("secret"));
}

#[test]
fn session_log_is_bounded_overwritten_and_never_contains_error_messages() {
    let parent = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("target");
    fs::create_dir_all(&parent).unwrap();
    let data = parent.join(format!("diagnostics-{}", std::process::id()));
    let mut log = SessionLog::open(&data).unwrap();
    log.advance(Stage::Payload).unwrap();
    let mut tail = OutputTail::new(PathBuf::from("/install/app"), data.clone());
    log.failure(&io::Error::other("secret /private/home"), Some(1), &tail)
        .unwrap();
    let path = data.join(".runtime").join("desktop-session.log");
    let text = fs::read_to_string(&path).unwrap();
    assert!(text.contains("Stage: Payload") && text.contains("Backend exit: Some(1)"));
    assert!(!text.contains("secret") && !text.contains("/private"));
    assert!(text.len() < 4096);
    // Installed refusals keep the UI alive, but retain the same sanitized readiness evidence.
    tail.feed(b"OVERSIGHT_REFUSAL=other\nValueError: secret /private/home\n");
    for (kind, exit) in [
        (io::ErrorKind::BrokenPipe, Some(3)),
        (io::ErrorKind::TimedOut, None),
    ] {
        log.advance(Stage::Readiness).unwrap();
        log.failure(&io::Error::from(kind), exit, &tail).unwrap();
        let text = fs::read_to_string(&path).unwrap();
        assert!(text.contains("Stage: Readiness"));
        assert!(text.contains(&format!("Kind: {kind:?}")));
        assert!(text.contains(&format!("Backend exit: {exit:?}")));
        assert!(text.contains("backend: ValueError"));
        assert!(!text.contains("secret") && !text.contains("/private"));
        assert!(!text.contains("OVERSIGHT_REFUSAL"));
    }
    log.advance(Stage::Git).unwrap();
    log.failure(&io::Error::other("PATH=secret /private/home"), None, &tail)
        .unwrap();
    let text = fs::read_to_string(&path).unwrap();
    assert!(text.contains("Hint: Git for Windows (git.exe) is required on PATH.\n"));
    assert!(!text.contains("secret") && !text.contains("/private") && !text.contains("PATH="));
    drop(log);
    drop(SessionLog::open(&data).unwrap());
    assert!(!fs::read_to_string(&path).unwrap().contains("failed"));
    #[cfg(unix)]
    {
        fs::remove_file(&path).unwrap();
        let outside = data.join("outside");
        fs::write(&outside, b"retained").unwrap();
        std::os::unix::fs::symlink(&outside, &path).unwrap();
        assert!(SessionLog::open(&data).is_err());
        assert_eq!(fs::read(outside).unwrap(), b"retained");
    }
    fs::remove_dir_all(data).unwrap();
}

#[test]
fn refusal_output_is_fragmented_bounded_and_closed_without_retaining_raw_text() {
    use oversight_desktop_core::refusal::Refusal;
    let mut tail = OutputTail::new("app".into(), "data".into());
    tail.feed(b"OVERSIGHT_REF");
    assert_eq!(tail.refusal(), None);
    tail.feed(b"USAL=previous-session-changed\r\n");
    assert_eq!(tail.refusal(), Some(Refusal::PreviousSessionChanged));
    tail.feed(b"OVERSIGHT_REFUSAL=garbage\n OVERSIGHT_REFUSAL=other\n");
    tail.feed(&vec![b'x'; 2048]);
    tail.feed(b"OVERSIGHT_REFUSAL=other\n");
    assert_eq!(tail.refusal(), Some(Refusal::PreviousSessionChanged));
    assert_eq!(tail.text(), "");
}

#[test]
fn no_child_never_shows_a_shutdown_warning_for_launch_or_port_errors() {
    use oversight_desktop_core::diagnostics::CloseOutcome;
    for kind in [io::ErrorKind::InvalidInput, io::ErrorKind::AddrInUse] {
        let outcome = CloseOutcome {
            primary: Err(io::Error::from(kind)),
            cleanup: None,
            reaped: false,
        };
        assert!(!outcome.unverified());
        assert_eq!(outcome.finish().unwrap_err().kind(), kind);
    }
}

#[test]
fn verified_shutdown_preserves_backend_failure_without_a_shutdown_warning() {
    use oversight_desktop_core::diagnostics::CloseOutcome;
    let outcome = CloseOutcome {
        primary: Err(io::Error::from(io::ErrorKind::BrokenPipe)),
        cleanup: Some(Ok(true)),
        reaped: true,
    };
    assert!(!outcome.unverified());
    assert_eq!(
        outcome.finish().unwrap_err().kind(),
        io::ErrorKind::BrokenPipe
    );
    let clean = CloseOutcome {
        primary: Ok(()),
        cleanup: Some(Ok(true)),
        reaped: true,
    };
    assert!(!clean.unverified());
    assert!(clean.finish().is_ok());
}

#[test]
fn forced_failed_or_unreaped_shutdown_warns_and_keeps_both_outcomes() {
    use oversight_desktop_core::diagnostics::CloseOutcome;
    for (cleanup, reaped) in [
        (Ok(false), true),
        (Err(io::Error::from(io::ErrorKind::PermissionDenied)), true),
        (Ok(true), false),
    ] {
        let outcome = CloseOutcome {
            primary: Err(io::Error::from(io::ErrorKind::BrokenPipe)),
            cleanup: Some(cleanup),
            reaped,
        };
        assert!(outcome.unverified());
        let error = outcome.finish().unwrap_err();
        let retained = error
            .get_ref()
            .unwrap()
            .downcast_ref::<CloseOutcome>()
            .unwrap();
        assert_eq!(
            retained.primary.as_ref().unwrap_err().kind(),
            io::ErrorKind::BrokenPipe
        );
        assert!(retained.cleanup.is_some());
        assert_eq!(retained.reaped, reaped);
    }
}
