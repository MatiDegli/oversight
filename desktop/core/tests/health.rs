use std::io::{ErrorKind, Read, Write};
use std::net::{SocketAddr, TcpListener};
use std::thread;
use std::time::{Duration, Instant};

use oversight_desktop_core::health::{MAX_RESPONSE, healthy, probe};

fn response(status: &str, body: &str) -> Vec<u8> {
    format!(
        "HTTP/1.1 {status}\r\nContent-Length: {}\r\n\r\n{body}",
        body.len()
    )
    .into_bytes()
}

#[test]
fn accepts_only_live_ok_200_with_complete_unambiguous_bounded_body() {
    let valid = response("200 OK", r#"{"ok":true,"mode":"live","run_id":"fixture"}"#);
    assert!(healthy(&valid));
    for (status, body) in [
        ("302 Redirect", r#"{"ok":true,"mode":"live"}"#),
        ("503 Unavailable", r#"{"ok":true,"mode":"live"}"#),
        ("2000 Nope", r#"{"ok":true,"mode":"live"}"#),
        ("200 OK", r#"{"ok":true,"mode":"replay"}"#),
        ("200 OK", r#"{"ok":false,"mode":"live"}"#),
        ("200 OK", r#"{"ok":"true","mode":"live"}"#),
        ("200 OK", r#"{"ok":true}"#),
        ("200 OK", "invalid json"),
    ] {
        assert!(
            !healthy(&response(status, body)),
            "accepted {status} {body}"
        );
    }
    for length in 0..valid.len() {
        assert!(
            !healthy(&valid[..length]),
            "accepted truncated response {length}"
        );
    }
    let mut oversized = valid.clone();
    oversized.resize(MAX_RESPONSE + 1, b' ');
    assert!(!healthy(&oversized));
    assert!(!healthy(b"HTTP/1.1 200 OK\r\n\r\n{}"));
    assert!(!healthy(
        &valid.iter().copied().chain([b' ']).collect::<Vec<_>>()
    ));
    let valid_text = String::from_utf8(valid).unwrap();
    for header in [
        "Transfer-Encoding: chunked",
        "Content-Encoding: gzip",
        "Content-Length: 0",
        "broken header",
    ] {
        assert!(!healthy(
            valid_text
                .replacen("\r\n", &format!("\r\n{header}\r\n"), 1)
                .as_bytes()
        ));
    }
}

fn server(
    serve: impl FnOnce(std::net::TcpStream) + Send + 'static,
) -> (SocketAddr, thread::JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let handle = thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        let mut request = Vec::new();
        let mut byte = [0];
        while !request.ends_with(b"\r\n\r\n") {
            stream.read_exact(&mut byte).unwrap();
            request.push(byte[0]);
            assert!(request.len() < 1024);
        }
        let request = String::from_utf8(request).unwrap();
        assert!(request.starts_with("GET /health HTTP/1.1\r\n"));
        assert!(request.contains(&format!("Host: {address}\r\n")));
        assert!(request.contains("Connection: close\r\n"));
        serve(stream);
    });
    (address, handle)
}

#[test]
fn loopback_probe_sends_same_origin_request_and_reads_fragmented_response() {
    let (address, server) = server(|mut stream| {
        for part in response("200 OK", r#"{"ok":true,"mode":"live"}"#).chunks(7) {
            stream.write_all(part).unwrap();
        }
    });
    assert!(probe(address, Instant::now() + Duration::from_secs(2)).unwrap());
    server.join().unwrap();
}

#[test]
fn stalled_and_slow_response_share_an_absolute_deadline() {
    for slow in [false, true] {
        let (address, server) = server(move |mut stream| {
            if slow {
                for _ in 0..30 {
                    if stream.write_all(b"H").is_err() {
                        break;
                    }
                    thread::sleep(Duration::from_millis(10));
                }
            } else {
                thread::sleep(Duration::from_millis(300));
            }
        });
        let start = Instant::now();
        assert!(probe(address, start + Duration::from_millis(100)).is_err());
        assert!(start.elapsed() < Duration::from_secs(2));
        server.join().unwrap();
    }
}

#[test]
fn expired_deadline_foreign_addresses_and_oversized_responses_are_refused() {
    assert_eq!(
        probe("127.0.0.1:8710".parse().unwrap(), Instant::now())
            .unwrap_err()
            .kind(),
        ErrorKind::TimedOut
    );
    assert_eq!(
        probe(
            "192.0.2.1:8710".parse().unwrap(),
            Instant::now() + Duration::from_secs(1)
        )
        .unwrap_err()
        .kind(),
        ErrorKind::InvalidInput
    );
    assert_eq!(
        probe(
            "127.0.0.1:0".parse().unwrap(),
            Instant::now() + Duration::from_secs(1)
        )
        .unwrap_err()
        .kind(),
        ErrorKind::InvalidInput
    );
    let (address, server) = server(|mut stream| {
        let _ = stream.write_all(&vec![b'X'; MAX_RESPONSE + 1]);
    });
    assert!(!probe(address, Instant::now() + Duration::from_secs(2)).unwrap());
    server.join().unwrap();
}
