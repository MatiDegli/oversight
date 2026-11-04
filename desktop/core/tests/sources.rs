use std::path::Path;

use oversight_desktop_core::refusal::Refusal;
use oversight_desktop_core::source::{SelectedSource, runtime_dir};

#[test]
fn selection_refuses_ambiguous_paths_before_filesystem_access() {
    for path in [
        "",
        "relative",
        "/tmp/repo",
        r"C:repo",
        r"C:/repo",
        r"C:\a\..\b",
        r"C:\a\.\b",
        r"\\server\repo",
        r"\\?\C:\repo",
        r"\\wsl$\Ubuntu\repo",
        r"C:\a~1\repo",
        r"C:\CON .txt\repo",
        r"C:\.git\repo",
        "C:\\repo\u{7f}",
        r"C:\conin$\repo",
        r"C:\CONOUT$.txt\repo",
        r"C:\nul\repo",
        r"C:\COM¹\repo",
        r"C:\LPT9\repo",
        r"C:\repo.\x",
        r"C:\repo \x",
    ] {
        assert!(SelectedSource::validate(Path::new(path)).is_err(), "{path}");
    }
    for path in [r"C:\private\repo", r"d:\space español 日本\repo"] {
        assert_eq!(
            SelectedSource::validate(Path::new(path)).unwrap().path(),
            Path::new(path)
        );
    }
}

#[test]
fn runtime_is_stable_case_normalized_short_sha256_and_demo_keeps_history() {
    let data = Path::new("data");
    let source = SelectedSource::validate(Path::new(r"C:\private\repo")).unwrap();
    let same = SelectedSource::validate(Path::new(r"c:\PRIVATE\Repo")).unwrap();
    assert_eq!(source.runtime_dir(data), same.runtime_dir(data));
    assert_eq!(
        source.runtime_dir(data),
        data.join("sources")
            .join("d7ecfa211fe06020")
            .join(".runtime")
    );
    let other = SelectedSource::validate(Path::new(r"C:\private\other")).unwrap();
    assert_ne!(source.runtime_dir(data), other.runtime_dir(data));
    assert_eq!(runtime_dir(data, None), data.join(".runtime"));
}

#[test]
fn refusal_vocabulary_is_exact_and_never_passes_arbitrary_output() {
    let categories = [
        ("not-standalone-repo", Refusal::NotStandaloneRepo),
        ("unsupported-location", Refusal::UnsupportedLocation),
        ("path-too-long", Refusal::PathTooLong),
        ("previous-session-changed", Refusal::PreviousSessionChanged),
        ("other", Refusal::Other),
    ];
    for (text, refusal) in categories {
        assert_eq!(
            Refusal::parse(&format!("OVERSIGHT_REFUSAL={text}")),
            Some(refusal)
        );
        assert_eq!(
            Refusal::parse(&format!("OVERSIGHT_REFUSAL={text}\r")),
            Some(refusal)
        );
    }
    for text in [
        "OVERSIGHT_REFUSAL=unknown",
        " OVERSIGHT_REFUSAL=other",
        "OVERSIGHT_REFUSAL=other ",
        "OVERSIGHT_REFUSAL=other\n",
        "OVERSIGHT_REFUSAL=other\r\r",
        "ERROR: secret",
        "prefix OVERSIGHT_REFUSAL=other",
        "OVERSIGHT_REFUSAL=OTHER",
        "OVERSIGHT_REFUSAL=",
    ] {
        assert_eq!(Refusal::parse(text), None, "{text:?}");
    }
    let remedy = Refusal::PreviousSessionChanged.remedy(Path::new("data/sources/123/.runtime"));
    for text in [
        "workspace",
        "main",
        "Git",
        "delete the per-source folder data/sources/123",
    ] {
        assert!(remedy.contains(text));
    }
}
