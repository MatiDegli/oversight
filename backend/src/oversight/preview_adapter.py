"""Trusted stdlib-only shop preview entry; provenance is not Python containment.

Launch by absolute path with the prepared demo interpreter, -I -B and an inherited
listener. The supervisor owns C, Git snapshots, leases and verified namespace drain.
This module starts no processes and owns no Oversight events or lifecycle.
"""

import argparse
import importlib
import io
import json
import os
import re
import secrets
import socket
import sys
from contextlib import suppress
from dataclasses import dataclass
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from time import monotonic
from typing import Any, NoReturn, cast
from urllib.parse import parse_qsl, urlsplit

MEMORY_BYTES = 536870912
BODY_BYTES = 4096
IO_TIMEOUT = 2
FIELDS = {"quantity_1", "quantity_2", "coupon", "nonce"}


def fatal(code: int) -> NoReturn:
    """No traceback, formatting or cleanup allocation on the exhausted-memory path."""
    os._exit(code)


def silence() -> None:
    fd = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(fd, 1)
        os.dup2(fd, 2)
    finally:
        if fd > 2:
            os.close(fd)


@dataclass(frozen=True)
class Config:
    listener_fd: int
    port: int
    worktree: Path
    memory_limit_bytes: int
    ready_nonce: str
    origins: tuple[str, ...]


def valid_origin(value: str) -> bool:
    if re.fullmatch(r"http://(?:localhost|127\.0\.0\.1|\[::1\])(?::[1-9][0-9]{0,4})?", value):
        port = urlsplit(value).port
        return port is None or 1 <= port <= 65535
    return False


def configuration(argv: list[str] | None = None) -> Config:
    parser = argparse.ArgumentParser(add_help=False, exit_on_error=False, allow_abbrev=False)
    parser.add_argument("--listener-fd", type=int, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--memory-limit-bytes", type=int, default=MEMORY_BYTES)
    parser.add_argument("--ready-nonce", required=True)
    parser.add_argument("--oversight-origin", action="append", required=True)
    args = parser.parse_args(argv)
    root = Path(args.worktree)
    if not root.is_absolute() or not root.resolve(strict=True).is_dir():
        raise ValueError
    origins = tuple(args.oversight_origin)
    if (
        not 1 <= args.port <= 65535
        or args.memory_limit_bytes <= 0
        or not re.fullmatch(r"[0-9a-f]{64}", args.ready_nonce)
        or not all(valid_origin(origin) for origin in origins)
    ):
        raise ValueError
    return Config(
        args.listener_fd,
        args.port,
        root.resolve(strict=True),
        args.memory_limit_bytes,
        args.ready_nonce,
        origins,
    )


def install_limit(limit: int) -> None:
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        if resource.getrlimit(resource.RLIMIT_AS) != (limit, limit):
            fatal(73)
    except (ImportError, AttributeError, OSError, ValueError, OverflowError, MemoryError):
        fatal(73)


def provenance(root: Path) -> None:
    try:
        for name, module in tuple(sys.modules.items()):
            if name == "shop" or name.startswith("shop."):
                filename = getattr(module, "__file__", None)
                if not isinstance(filename, str):
                    fatal(72)
                path = Path(filename).resolve(strict=True)
                if not path.is_file() or not path.is_relative_to(root):
                    fatal(72)
    except (OSError, ValueError, RuntimeError):
        fatal(72)


class Shop:
    def __init__(self, root: Path):
        self.root = root
        provenance(root)  # Also reject foreign or incomplete preloaded modules.
        sys.path.insert(0, str(root))
        try:
            self.cart = importlib.import_module("shop.cart")
            provenance(root)
            self.models = importlib.import_module("shop.models")
            provenance(root)
            self.pricing = importlib.import_module("shop.pricing")
        except ModuleNotFoundError as error:
            if error.name == "shop" or (error.name and error.name.startswith("shop.")):
                fatal(72)
            raise
        finally:
            provenance(root)

    def call(self, function: Any, *args: Any) -> Any:
        try:
            return function(*args)
        finally:
            provenance(self.root)

    def total(self, quantities: tuple[int, int], coupon: str) -> tuple[int, str]:
        cart = self.call(self.cart.Cart)
        for sku, name, price, quantity in zip(
            ("one", "two"), ("Product one", "Product two"), (1000, 500), quantities, strict=True
        ):
            if quantity:
                product = self.call(self.models.Product, sku, name, price)
                self.call(cart.add, product, quantity)
        subtotal = self.call(cart.total)
        if type(subtotal) is not int:
            raise ValueError
        total = self.call(self.pricing.apply_coupon, subtotal, coupon)
        if type(total) is not int:
            raise ValueError
        formatted = self.call(self.models.format_cents, total)
        return total, self.call(str, formatted)


def adopt(config: Config) -> socket.socket:
    fd = config.listener_fd
    if fd < 3:
        fatal(71)
    listener = None
    try:
        listener = socket.socket(fileno=fd)
        if (
            listener.family != socket.AF_INET
            or listener.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM
            or not listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
            or listener.getsockname() != ("127.0.0.1", config.port)
        ):
            raise ValueError
        listener.set_inheritable(False)
        return listener
    except (OSError, ValueError):
        if listener is not None:
            listener.close()
        else:
            with suppress(OSError):
                os.close(fd)
        fatal(71)


def form_data(raw: bytes) -> tuple[tuple[int, int], str, str]:
    text = raw.decode("ascii")
    if re.search(r"%(?![0-9a-fA-F]{2})", text):
        raise ValueError
    pairs = parse_qsl(
        text,
        keep_blank_values=True,
        strict_parsing=True,
        encoding="utf-8",
        errors="strict",
        max_num_fields=4,
    )
    data = dict(pairs)
    if len(pairs) != 4 or set(data) != FIELDS:
        raise ValueError
    quantities: list[int] = []
    for field in ("quantity_1", "quantity_2"):
        value = data[field]
        if not re.fullmatch(r"[0-9]{1,2}", value):
            raise ValueError
        quantities.append(int(value))
    if len(data["coupon"]) > 64 or not re.fullmatch(r"[0-9a-f]{64}", data["nonce"]):
        raise ValueError
    return (quantities[0], quantities[1]), data["coupon"], data["nonce"]


def page(quantities: tuple[int, int], coupon: str, nonce: str, total: int, amount: str) -> bytes:
    controls = "".join(
        f'<p><label for="quantity_{i}">Product {i} ({price} cents)</label> '
        f'<input id="quantity_{i}" name="quantity_{i}" type="number" min="0" max="99" '
        f'value="{quantity}" required></p>'
        for i, price, quantity in zip((1, 2), (1000, 500), quantities, strict=True)
    )
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Candidate shop</title>'
        '<body><h1>Candidate shop</h1><form action="/cart" method="post">'
        f'{controls}<p><label for="coupon">Coupon</label> '
        f'<input id="coupon" name="coupon" maxlength="64" value="{escape(coupon, quote=True)}">'
        f'</p><input type="hidden" name="nonce" value="{nonce}">'
        '<button type="submit">Update cart</button></form>'
        f'<p>Total: <output data-total-cents="{total}">{escape(amount)}</output> '
        f"({total} cents)</p></body></html>"
    ).encode()


class Server(HTTPServer):
    def __init__(self, config: Config, shop: Shop, listener: socket.socket):
        self.config = config
        self.shop = shop
        self.nonce = ""
        try:
            super().__init__(("127.0.0.1", config.port), Handler, bind_and_activate=False)
            self.socket.close()
            self.socket = listener
            self.server_address = ("127.0.0.1", config.port)
            self.server_name = "127.0.0.1"
            self.server_port = config.port
        except BaseException:
            listener.close()
            raise

    def get_request(self) -> tuple[socket.socket, Any]:
        connection, address = self.socket.accept()
        connection.settimeout(IO_TIMEOUT)
        return connection, address

    def handle_error(self, request: Any, client_address: Any) -> None:
        if isinstance(sys.exception(), MemoryError):
            fatal(74)
        # Never publish exceptions originating from candidate code.


class DeadlineReader(socket.SocketIO):
    """Apply one read deadline even when buffered reads receive a slow byte stream."""

    def __init__(self, connection: socket.socket):
        super().__init__(connection, "rb")
        self.connection = connection
        self.deadline = monotonic() + IO_TIMEOUT

    def readinto(self, b: Any) -> int | None:
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError
        self.connection.settimeout(remaining)
        return super().readinto(b)


class Handler(BaseHTTPRequestHandler):
    def setup(self) -> None:
        super().setup()
        self.rfile.close()
        self.rfile = io.BufferedReader(DeadlineReader(self.connection))

    @property
    def preview(self) -> Server:
        return cast(Server, self.server)

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        self.respond(code, b"Preview request refused.\n", "text/plain; charset=utf-8")

    def respond(self, code: int, body: bytes, content_type: str) -> None:
        provenance(self.preview.config.worktree)
        if self.request_version == "HTTP/0.9":
            self.request_version = "HTTP/1.0"
        self.close_connection = True
        self.send_response_only(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; form-action 'self'; "
            "base-uri 'none'; frame-ancestors " + " ".join(self.preview.config.origins),
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.connection.settimeout(IO_TIMEOUT)
        self.end_headers()
        self.wfile.write(body)

    def handle_one_request(self) -> None:
        try:
            self.raw_requestline = self.rfile.readline(65537)
            if not self.raw_requestline:
                self.close_connection = True
                return
            if len(self.raw_requestline) > 65536:
                self.request_version = "HTTP/1.0"
                self.send_error(414)
                return
            if not self.parse_request():
                return
            if self.request_version == "HTTP/0.9":
                self.request_version = "HTTP/1.0"
                self.send_error(400)
                return
            self.dispatch()
        except MemoryError:
            fatal(74)
        except TimeoutError:
            self.request_version = "HTTP/1.0"
            self.send_error(408)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            self.send_error(500)

    def dispatch(self) -> None:
        config = self.preview.config
        origins = self.headers.get_all("Origin", [])
        if self.headers.get_all("Host", []) != [f"127.0.0.1:{config.port}"]:
            self.send_error(403)
            return
        is_cart = self.command == "POST" and self.path == "/cart"
        if (is_cart and origins != ["null"]) or (
            not is_cart and origins and (len(origins) != 1 or origins[0] not in config.origins)
        ):
            self.send_error(403)
            return
        if self.command == "GET" and self.path in ("/", "/_oversight_ready"):
            if self.headers.get_all("Transfer-Encoding") or self.headers.get_all("Content-Length"):
                self.send_error(400)
            elif self.path == "/_oversight_ready":
                self.respond(
                    200, json.dumps({"nonce": config.ready_nonce}).encode(), "application/json"
                )
            else:
                self.render((1, 1), "")
        elif is_cart:
            self.submit_cart()
        else:
            self.send_error(404 if self.command in ("GET", "POST") else 405)

    def submit_cart(self) -> None:
        lengths = self.headers.get_all("Content-Length", [])
        if (
            self.headers.get_all("Transfer-Encoding")
            or self.headers.get_all("Expect")
            or self.headers.get_all("Content-Type", []) != ["application/x-www-form-urlencoded"]
            or len(lengths) != 1
            or not re.fullmatch(r"[0-9]{1,4}", lengths[0])
        ):
            self.send_error(400)
            return
        length = int(lengths[0])
        if length > BODY_BYTES:
            self.send_error(413)
            return
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError
            quantities, coupon, nonce = form_data(raw)
        except (ValueError, UnicodeError):
            self.send_error(400)
            return
        if not self.preview.nonce or not secrets.compare_digest(nonce, self.preview.nonce):
            self.send_error(403)
            return
        self.preview.nonce = ""
        self.render(quantities, coupon)

    def render(self, quantities: tuple[int, int], coupon: str) -> None:
        total, amount = self.preview.shop.total(quantities, coupon)
        nonce = secrets.token_hex(32)
        body = page(quantities, coupon, nonce, total, amount)
        self.preview.nonce = nonce
        self.respond(200, body, "text/html; charset=utf-8")


def main(argv: list[str] | None = None) -> None:
    silence()
    try:
        config = configuration(argv)
    except (Exception, SystemExit):
        fatal(70)
    install_limit(config.memory_limit_bytes)
    try:
        shop = Shop(config.worktree)
        with Server(config, shop, adopt(config)) as server:
            server.serve_forever()
    except MemoryError:
        fatal(74)
    except Exception:
        fatal(1)


if __name__ == "__main__":
    main()
