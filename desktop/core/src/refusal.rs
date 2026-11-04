//! Only this exact closed vocabulary crosses the backend output boundary.
use std::path::Path;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Refusal {
    NotStandaloneRepo,
    UnsupportedLocation,
    PathTooLong,
    PreviousSessionChanged,
    Other,
}

impl Refusal {
    pub fn parse(line: &str) -> Option<Self> {
        match line.strip_suffix('\r').unwrap_or(line) {
            "OVERSIGHT_REFUSAL=not-standalone-repo" => Some(Self::NotStandaloneRepo),
            "OVERSIGHT_REFUSAL=unsupported-location" => Some(Self::UnsupportedLocation),
            "OVERSIGHT_REFUSAL=path-too-long" => Some(Self::PathTooLong),
            "OVERSIGHT_REFUSAL=previous-session-changed" => Some(Self::PreviousSessionChanged),
            "OVERSIGHT_REFUSAL=other" => Some(Self::Other),
            _ => None,
        }
    }

    pub fn remedy(self, runtime: &Path) -> String {
        match self {
            Self::NotStandaloneRepo => "Select a standalone Git repository with its own .git directory. Linked worktrees and bare repositories cannot be opened.".into(),
            Self::UnsupportedLocation => "Move the repository to a private local Windows drive folder outside synced folders, without links or reserved names, then try again.".into(),
            Self::PathTooLong => "Move the repository to a shorter local path, then try again.".into(),
            Self::PreviousSessionChanged => {
                let source_folder = runtime.parent().filter(|parent| {
                    let container = parent.parent().and_then(Path::file_name);
                    container.and_then(|name| name.to_str())
                        .is_some_and(|name| name.eq_ignore_ascii_case("sources"))
                });
                let (folder, label) = source_folder.map_or(
                    (runtime, "runtime folder"),
                    |parent| (parent, "per-source folder"),
                );
                format!(
                    "The earlier session has approved changes in {}. Its workspace folder is a Git repository whose main holds the approved changes. Copy them out with Git, then delete the {} {} to open the repository fresh.",
                    runtime.display(), label, folder.display()
                )
            }
            Self::Other => "Oversight could not open this session. Check that the repository and retained state are accessible, close other Oversight sessions, then try again. Preserve earlier work before removing any state.".into(),
        }
    }
}
