"""Validate before touching buffers or sockets; zero results verified on d31d821."""

import socket
from contextlib import contextmanager
from importlib import import_module

import pytest

BufferedSocket = import_module("boltons.socketutils").BufferedSocket


class Index:
    def __index__(self):
        return 3


@contextmanager
def connection():
    reader, writer = socket.socketpair()
    reader.settimeout(0.5)
    writer.settimeout(0.5)
    try:
        writer.sendall(b"abcdef")
        yield BufferedSocket(reader, timeout=0.5)
    finally:
        reader.close()
        writer.close()


@pytest.mark.parametrize("method", ["recv", "peek", "recv_size"])
@pytest.mark.parametrize("buffered", [False, True])
@pytest.mark.parametrize("size,error", [(-1, ValueError), (2.5, TypeError)])
def test_invalid_size_preserves_all_data(method, buffered, size, error):
    with connection() as sock:
        if buffered:
            assert sock.peek(6) == b"abcdef"
        with pytest.raises(error):
            getattr(sock, method)(size)
        assert sock.recv_size(6) == b"abcdef"


@pytest.mark.parametrize("method", ["recv", "peek", "recv_size"])
def test_index_protocol_and_zero_compatibility(method):
    with connection() as sock:
        assert getattr(sock, method)(0) == b""
        assert getattr(sock, method)(Index()) == b"abc"
        assert sock.recv_size(6 if method == "peek" else 3) == (
            b"abcdef" if method == "peek" else b"def"
        )
