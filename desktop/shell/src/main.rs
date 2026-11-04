#[cfg(windows)]
mod child;
#[cfg(windows)]
mod console;
#[cfg(windows)]
mod diagnostics;
#[cfg(windows)]
mod dialogs;
#[cfg(windows)]
mod listener;
#[cfg(windows)]
mod menu;
#[cfg(windows)]
mod native;
#[cfg(windows)]
mod output;
#[cfg(windows)]
mod session;
#[cfg(windows)]
mod settings;
#[cfg(windows)]
mod supervise;
#[cfg(windows)]
mod window;

#[cfg(not(windows))]
fn main() {
    eprintln!("Oversight desktop is Windows-only.");
    std::process::exit(2);
}

#[cfg(windows)]
fn main() {
    if native::run().is_err() {
        std::process::exit(1);
    }
}
