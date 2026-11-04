use oversight_desktop_core::diagnostics::{OutputTail, SessionLog, Stage, finish};
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
fn cleanup_never_masks_the_startup_error_or_accepts_forced_shutdown() {
    let error = finish(
        Err(io::Error::from(io::ErrorKind::InvalidInput)),
        Err(io::Error::from(io::ErrorKind::PermissionDenied)),
    )
    .unwrap_err();
    assert_eq!(error.kind(), io::ErrorKind::InvalidInput);
    assert!(finish(Ok(()), Ok(false)).is_err());
    assert!(
        finish(
            Ok(()),
            Err(io::Error::from(io::ErrorKind::PermissionDenied))
        )
        .is_err()
    );
    assert!(finish(Ok(()), Ok(true)).is_ok());
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
    let tail = OutputTail::new(PathBuf::from("/install/app"), data.clone());
    log.failure(&io::Error::other("secret /private/home"), Some(1), &tail)
        .unwrap();
    let path = data.join(".runtime/desktop-session.log");
    let text = fs::read_to_string(&path).unwrap();
    assert!(text.contains("Stage: Payload") && text.contains("Backend exit: Some(1)"));
    assert!(!text.contains("secret") && !text.contains("/private"));
    assert!(text.len() < 4096);
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
