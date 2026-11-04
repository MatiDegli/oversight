#[cfg(windows)]
mod child;
#[cfg(windows)]
mod console;
#[cfg(windows)]
mod diagnostics;
#[cfg(windows)]
mod listener;
#[cfg(windows)]
mod native;
#[cfg(windows)]
mod output;
#[cfg(windows)]
mod supervise;

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
