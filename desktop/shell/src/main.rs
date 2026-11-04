#[cfg(windows)]
mod child;
#[cfg(windows)]
mod listener;
#[cfg(windows)]
mod native;
#[cfg(windows)]
mod supervise;

#[cfg(not(windows))]
fn main() {
    eprintln!("Oversight desktop is Windows-only.");
    std::process::exit(2);
}

#[cfg(windows)]
fn main() {
    if let Err(error) = native::run() {
        eprintln!("Oversight desktop: {error}");
        std::process::exit(1);
    }
}
