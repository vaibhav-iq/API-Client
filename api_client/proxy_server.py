"""A small intercepting HTTP/HTTPS proxy for capturing traffic (Burp-style).

Browsers/apps point their proxy at 127.0.0.1:<port>. Plain HTTP is forwarded
directly; HTTPS is intercepted via `CONNECT` + TLS MITM using leaf certificates
signed by the app's own CA (see ca.py), so the request/response can be captured.

For authorized testing only. Runs each connection on its own thread; captured
exchanges are delivered to the UI through a Qt signal.
"""
import itertools
import socket
import socketserver
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple
from urllib.parse import urlsplit

import requests
from PyQt5.QtCore import QObject, pyqtSignal

from .ca import CertAuthority
from .config_store import AppSettings

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "proxy-connection",
}
MAX_CAPTURE_BYTES = 3 * 1024 * 1024


@dataclass
class CaptureRecord:
    id: int = 0
    method: str = "GET"
    url: str = ""
    host: str = ""
    path: str = ""
    scheme: str = "http"
    status: Optional[int] = None
    reason: str = ""
    req_headers: List[Tuple[str, str]] = field(default_factory=list)
    req_body: bytes = b""
    resp_headers: List[Tuple[str, str]] = field(default_factory=list)
    resp_body: bytes = b""
    content_type: str = ""
    elapsed_ms: float = 0.0
    error: str = ""
    highlight: str = ""  # colour name for row highlighting (Burp-style)
    ip: str = ""
    edited: bool = False
    orig_method: str = ""
    orig_url: str = ""
    orig_req_headers: List[Tuple[str, str]] = field(default_factory=list)
    orig_req_body: bytes = b""
    resp_edited: bool = False
    orig_status: Optional[int] = None
    orig_reason: str = ""
    orig_resp_headers: List[Tuple[str, str]] = field(default_factory=list)
    orig_resp_body: bytes = b""
    started_at: float = field(default_factory=time.time)


def _parse_request(rfile) -> Optional[Tuple[str, str, str, List[Tuple[str, str]]]]:
    line = rfile.readline()
    if not line:
        return None
    try:
        text = line.decode("latin-1").rstrip("\r\n")
    except Exception:  # noqa: BLE001
        return None
    parts = text.split(" ")
    if len(parts) < 3:
        return None
    method, target, version = parts[0], parts[1], parts[2]
    headers: List[Tuple[str, str]] = []
    while True:
        hline = rfile.readline()
        if not hline or hline in (b"\r\n", b"\n"):
            break
        try:
            name, _, value = hline.decode("latin-1").rstrip("\r\n").partition(":")
        except Exception:  # noqa: BLE001
            continue
        if name:
            headers.append((name.strip(), value.strip()))
    return method, target, version, headers


def _header(headers: List[Tuple[str, str]], name: str) -> Optional[str]:
    low = name.lower()
    for key, value in headers:
        if key.lower() == low:
            return value
    return None


def _read_body(rfile, headers: List[Tuple[str, str]]) -> bytes:
    te = (_header(headers, "Transfer-Encoding") or "").lower()
    if "chunked" in te:
        chunks = []
        while True:
            size_line = rfile.readline()
            if not size_line:
                break
            try:
                size = int(size_line.strip().split(b";")[0], 16)
            except ValueError:
                break
            if size == 0:
                rfile.readline()  # trailing CRLF
                break
            chunks.append(rfile.read(size))
            rfile.readline()  # CRLF after chunk
        return b"".join(chunks)
    length = _header(headers, "Content-Length")
    if length and length.isdigit():
        return rfile.read(int(length))
    return b""


class InterceptedRequest:
    """A request paused by the interceptor, awaiting the user's Forward/Drop."""

    kind = "request"

    def __init__(self, method: str, url: str, headers: List[Tuple[str, str]], body: bytes, scheme: str) -> None:
        self.method = method
        self.url = url
        self.headers = headers
        self.body = body or b""
        self.scheme = scheme
        self.event = threading.Event()
        self.action = "forward"  # "forward" | "drop"
        self.intercept_response = False  # also hold this request's response (Burp "Do intercept")

    @property
    def host(self) -> str:
        return urlsplit(self.url).hostname or ""

    @property
    def path(self) -> str:
        sp = urlsplit(self.url)
        return sp.path + (f"?{sp.query}" if sp.query else "")

    def raw(self) -> str:
        lines = [f"{self.method} {self.path or '/'} HTTP/1.1"]
        lines.extend(f"{k}: {v}" for k, v in self.headers)
        lines.append("")
        if self.body:
            lines.append(self.body.decode("utf-8", "replace"))
        return "\n".join(lines)

    def apply_raw(self, text: str) -> None:
        parts = text.split("\n")
        if not parts:
            return
        first = parts[0].split()
        if first:
            self.method = first[0].upper()
        path = first[1] if len(first) > 1 else urlsplit(self.url).path
        headers: List[Tuple[str, str]] = []
        i = 1
        while i < len(parts) and parts[i].strip() != "":
            if ":" in parts[i]:
                name, _, value = parts[i].partition(":")
                if name.strip():
                    headers.append((name.strip(), value.strip()))
            i += 1
        body = "\n".join(parts[i + 1:]) if i + 1 <= len(parts) else ""
        self.headers = headers
        self.body = body.encode("utf-8")
        if path.startswith(("http://", "https://")):
            self.url = path
        else:
            self.url = f"{self.scheme}://{urlsplit(self.url).netloc}{path}"


class InterceptedResponse:
    """An upstream response paused by the interceptor before reaching the client."""

    kind = "response"

    def __init__(self, status: int, reason: str, http_version: str,
                 headers: List[Tuple[str, str]], body: bytes, label: str = "") -> None:
        self.status = status
        self.reason = reason
        self.http_version = http_version or "HTTP/1.1"
        self.headers = headers
        self.body = body or b""
        self.label = label
        self.event = threading.Event()
        self.action = "forward"  # "forward" | "drop"

    def raw(self) -> str:
        lines = [f"{self.http_version} {self.status} {self.reason}".rstrip()]
        lines.extend(f"{k}: {v}" for k, v in self.headers)
        lines.append("")
        if self.body:
            lines.append(self.body.decode("utf-8", "replace"))
        return "\n".join(lines)

    def apply_raw(self, text: str) -> None:
        parts = text.split("\n")
        first = parts[0].split(None, 2)
        if len(first) >= 2:
            self.http_version = first[0]
            try:
                self.status = int(first[1])
            except ValueError:
                pass
            self.reason = first[2].strip() if len(first) > 2 else self.reason
        headers: List[Tuple[str, str]] = []
        i = 1
        while i < len(parts) and parts[i].strip() != "":
            if ":" in parts[i]:
                name, _, value = parts[i].partition(":")
                if name.strip():
                    headers.append((name.strip(), value.strip()))
            i += 1
        body = "\n".join(parts[i + 1:]) if i + 1 <= len(parts) else ""
        self.headers = headers
        self.body = body.encode("utf-8")


class _Handler(socketserver.StreamRequestHandler):
    timeout = 120

    def handle(self) -> None:
        try:
            parsed = _parse_request(self.rfile)
        except Exception:  # noqa: BLE001
            return
        if parsed is None:
            return
        method, target, _version, headers = parsed
        session = requests.Session()
        session.trust_env = False
        try:
            if method.upper() == "CONNECT":
                self._handle_connect(target, session)
            else:
                body = _read_body(self.rfile, headers)
                self._forward(self.wfile, method, target, headers, body, "http", session)
        except (ConnectionError, OSError):
            pass
        finally:
            session.close()

    # -- HTTPS MITM --------------------------------------------------------------
    def _handle_connect(self, target: str, session: requests.Session) -> None:
        host, _, port_s = target.partition(":")
        port = port_s or "443"
        self.connection.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        try:
            context = self.server.ca.context_for(host)
            tls = context.wrap_socket(self.connection, server_side=True)
        except Exception:  # noqa: BLE001 - handshake failure (CA not trusted, pinning, …)
            return
        tls.settimeout(self.timeout)
        rfile = tls.makefile("rb")
        try:
            while True:
                parsed = _parse_request(rfile)
                if parsed is None:
                    break
                method, path, _version, headers = parsed
                body = _read_body(rfile, headers)
                netloc = host if port == "443" else f"{host}:{port}"
                url = f"https://{netloc}{path}"
                keep = self._forward(tls, method, url, headers, body, "https", session, raw_socket=True)
                if not keep:
                    break
        finally:
            try:
                tls.close()
            except OSError:
                pass

    # -- forward + capture -------------------------------------------------------
    def _forward(self, out, method: str, target: str, headers: List[Tuple[str, str]],
                 body: bytes, scheme: str, session: requests.Session, raw_socket: bool = False) -> bool:
        split = urlsplit(target if "://" in target else f"{scheme}://{target}")
        host = split.hostname or _header(headers, "Host") or ""
        url = target if "://" in target else f"{scheme}://{_header(headers, 'Host') or ''}{target}"

        orig = (method.upper(), url, list(headers), body)

        controller = self.server.controller
        intercept_response = False
        if controller.intercept_on():
            held = InterceptedRequest(method.upper(), url, list(headers), body, scheme)
            if not controller.submit_intercept(held):
                self._write_simple(out, 502, "Dropped", b"Request dropped by the interceptor.", raw_socket)
                return False
            method, url, headers, body = held.method, held.url, held.headers, held.body
            split = urlsplit(url)
            host = split.hostname or host
            intercept_response = held.intercept_response

        edited = (method.upper(), url, list(headers), body) != orig
        try:
            ip = socket.gethostbyname(host) if host else ""
        except OSError:
            ip = ""

        record = CaptureRecord(method=method.upper(), url=url, host=host, path=split.path or target,
                               scheme=scheme, req_headers=list(headers), req_body=body[:MAX_CAPTURE_BYTES],
                               ip=ip, edited=edited)
        if edited:
            record.orig_method, record.orig_url, record.orig_req_headers, record.orig_req_body = \
                orig[0], orig[1], orig[2], orig[3][:MAX_CAPTURE_BYTES]

        fwd_headers = {k: v for k, v in headers if k.lower() not in HOP_BY_HOP}
        settings = self.server.get_settings()
        timeout = max(1.0, settings.request_timeout_ms / 1000.0) if settings.request_timeout_ms else 60.0
        started = time.perf_counter()
        try:
            resp = session.request(method, url, headers=fwd_headers, data=body or None,
                                   allow_redirects=False, stream=True, timeout=timeout,
                                   verify=settings.ssl_verification)
            content = resp.raw.read(MAX_CAPTURE_BYTES + 1, decode_content=True)
            truncated = len(content) > MAX_CAPTURE_BYTES
            content = content[:MAX_CAPTURE_BYTES]
        except Exception as exc:  # noqa: BLE001
            record.error = f"{type(exc).__name__}: {exc}"
            self._emit(record)
            self._write_simple(out, 502, "Bad Gateway", f"Proxy error: {exc}".encode("utf-8"), raw_socket)
            return False

        status = resp.status_code
        reason = resp.reason or ""
        resp_headers = list(resp.headers.items())

        if intercept_response:
            orig_resp = (status, reason, list(resp_headers), content)
            held_resp = InterceptedResponse(status, reason, "HTTP/1.1", resp_headers, content,
                                            label=f"{method.upper()} {host}{split.path or ''}")
            if not controller.submit_response(held_resp):
                return False  # response dropped by the interceptor — close without replying
            status, reason, resp_headers, content = held_resp.status, held_resp.reason, held_resp.headers, held_resp.body
            if (status, reason, list(resp_headers), content) != orig_resp:
                record.resp_edited = True
                record.orig_status, record.orig_reason = orig_resp[0], orig_resp[1]
                record.orig_resp_headers = orig_resp[2]
                record.orig_resp_body = orig_resp[3][:MAX_CAPTURE_BYTES]

        record.elapsed_ms = (time.perf_counter() - started) * 1000.0
        record.status = status
        record.reason = reason
        record.resp_headers = resp_headers
        record.content_type = next((v for k, v in resp_headers if k.lower() == "content-type"), "")
        record.resp_body = content
        self._emit(record)

        # Write the response back to the client (recompute length, drop encodings).
        out_headers = [(k, v) for k, v in resp_headers
                       if k.lower() not in HOP_BY_HOP and k.lower() not in ("content-length", "content-encoding")]
        keep_alive = _wants_keep_alive(headers) and not truncated
        out_headers.append(("Content-Length", str(len(content))))
        out_headers.append(("Connection", "keep-alive" if keep_alive else "close"))
        self._write_response(out, status, reason, out_headers, content, raw_socket)
        return keep_alive

    def _emit(self, record: CaptureRecord) -> None:
        record.id = next(self.server.counter)
        try:
            self.server.controller.captured.emit(record)
        except RuntimeError:
            pass

    @staticmethod
    def _write_response(out, status: int, reason: str, headers: List[Tuple[str, str]], body: bytes, raw_socket: bool) -> None:
        head = [f"HTTP/1.1 {status} {reason}".encode("latin-1")]
        head.extend(f"{k}: {v}".encode("latin-1", "replace") for k, v in headers)
        data = b"\r\n".join(head) + b"\r\n\r\n" + body
        if raw_socket:
            out.sendall(data)
        else:
            out.write(data)
            out.flush()

    def _write_simple(self, out, status: int, reason: str, body: bytes, raw_socket: bool) -> None:
        self._write_response(out, status, reason, [("Content-Type", "text/plain"),
                                                   ("Content-Length", str(len(body))), ("Connection", "close")], body, raw_socket)


def _wants_keep_alive(headers: List[Tuple[str, str]]) -> bool:
    return (_header(headers, "Connection") or "keep-alive").lower() != "close"


def local_ip() -> str:
    """Best-effort primary LAN IPv4 address of this machine."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return "127.0.0.1"


def list_bind_addresses() -> List[str]:
    """Candidate bind addresses to offer in Settings (loopback, all, detected IPs)."""
    addrs = ["127.0.0.1", "0.0.0.0"]
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in addrs and not ip.startswith("127."):
                addrs.append(ip)
    except OSError:
        pass
    lan = local_ip()
    if lan not in addrs and lan != "127.0.0.1":
        addrs.append(lan)
    return addrs


class _ProxyTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class ProxyController(QObject):
    """Owns the proxy server lifecycle and relays captures to the UI thread."""

    captured = pyqtSignal(object)   # CaptureRecord
    started = pyqtSignal(int)       # port
    stopped = pyqtSignal()
    failed = pyqtSignal(str)
    intercepted = pyqtSignal(object)      # InterceptedRequest (held, awaiting the user)
    intercept_changed = pyqtSignal(bool)  # intercept on/off

    def __init__(self, ca: CertAuthority, get_settings: Callable[[], AppSettings]) -> None:
        super().__init__()
        self.ca = ca
        self.get_settings = get_settings
        self._server: Optional[_ProxyTCPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.port = 0
        self.host = "127.0.0.1"
        self._intercept = False
        self._pending: set = set()
        self._ilock = threading.Lock()

    # -- interception ------------------------------------------------------------
    def intercept_on(self) -> bool:
        return self._intercept

    def set_intercept(self, on: bool) -> None:
        on = bool(on)
        if on == self._intercept:
            return
        self._intercept = on
        if not on:
            self.release_all("forward")
        self.intercept_changed.emit(on)

    def submit_intercept(self, held: "InterceptedRequest") -> bool:
        """Called on a proxy worker thread; blocks until the UI forwards/drops."""
        if not self._intercept:
            return True
        with self._ilock:
            self._pending.add(held)
        self.intercepted.emit(held)
        held.event.wait()
        with self._ilock:
            self._pending.discard(held)
        return held.action == "forward"

    def submit_response(self, held: "InterceptedResponse") -> bool:
        """Hold an upstream response (requested per-request via 'intercept response')."""
        with self._ilock:
            self._pending.add(held)
        self.intercepted.emit(held)
        held.event.wait()
        with self._ilock:
            self._pending.discard(held)
        return held.action == "forward"

    def release_all(self, action: str = "forward") -> None:
        with self._ilock:
            pending = list(self._pending)
            self._pending.clear()
        for held in pending:
            held.action = action
            held.event.set()

    @property
    def running(self) -> bool:
        return self._server is not None

    def display_address(self) -> str:
        """Human-friendly listen address; resolves 0.0.0.0 to a LAN IP for convenience."""
        host = self.host
        if host in ("0.0.0.0", "::"):
            host = local_ip()
        return f"{host}:{self.port}"

    def start(self, port: int, host: str = "127.0.0.1") -> None:
        if self.running:
            return
        self.ca.ensure()
        try:
            server = _ProxyTCPServer((host, port), _Handler)
        except OSError as exc:
            self.failed.emit(f"Could not listen on {host}:{port} — {exc}")
            return
        server.ca = self.ca
        server.controller = self
        server.get_settings = self.get_settings
        server.counter = itertools.count(1)
        self._server = server
        self.port = port
        self.host = host
        self._thread = threading.Thread(target=server.serve_forever, name="proxy", daemon=True)
        self._thread.start()
        self.started.emit(port)

    def stop(self) -> None:
        self.release_all("forward")  # unblock any paused worker threads
        server, self._server = self._server, None
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception:  # noqa: BLE001
                pass
        self._thread = None
        self.stopped.emit()
