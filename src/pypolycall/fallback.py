"""Pure-Python implementation of polycall-peer/1 (docs/PEER_PROTOCOL.md).

Used when the native libpolycall is unavailable -- the NSIGII "HTTP
fallback": the network keeps working with the same wire protocol, limits
and semantics as the C node, so a Python node without the library and a C
node interoperate. Standard library only.
"""
from __future__ import annotations

import base64
import binascii
import collections
import hmac
import http.client
import json
import re
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .errors import (E_AUTH, E_BUSY, E_CANCELLED, E_CLOSED, E_CONFIG, E_INVALID_ARGUMENT,
                     E_INVALID_HANDLE, E_NOT_FOUND, E_PROTOCOL, E_REMOTE, E_TIMEOUT,
                     E_TOO_LARGE, E_TRANSPORT, PolycallError)

PROTOCOL = "polycall-peer/1"
ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,63}$")
EP_RE = re.compile(r"^[A-Za-z0-9._-]+:([0-9]{1,5})$")
MAX_PAYLOAD = 1 << 20
MAX_BODY = 2 << 20
MAX_HEADER = 16 * 1024
INBOX_MAX = 256
INBOX_BYTES = 64 << 20
DEDUP = 1024
MAX_CONNS = 32


def valid_id(s: object) -> bool:
    return isinstance(s, str) and bool(ID_RE.match(s))


def valid_endpoint(s: object, allow_zero: bool = False) -> bool:
    if not isinstance(s, str):
        return False
    m = EP_RE.match(s)
    return bool(m) and (0 if allow_zero else 1) <= int(m.group(1)) <= 65535


def split_endpoint(ep: str) -> tuple[str, int]:
    host, _, port = ep.rpartition(":")
    return host, int(port)


def _loopback(host: str) -> bool:
    return host in ("localhost",) or host.startswith("127.")


class _Message:
    __slots__ = ("sender", "message_id", "payload")

    def __init__(self, sender: str, message_id: str, payload: bytes) -> None:
        self.sender, self.message_id, self.payload = sender, message_id, payload


class _Handler(BaseHTTPRequestHandler):
    server_version = "polycall-peer/1"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 10                      # per-socket read deadline (slow clients)

    node: "FallbackPeer"

    def log_message(self, fmt: str, *args: object) -> None:  # never log bodies/tokens
        return

    # ---- responses --------------------------------------------------------
    def _send(self, status: int, obj: dict, extra: dict | None = None) -> None:
        body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _err(self, status: int, code: str, message: str, extra: dict | None = None) -> None:
        self._send(status, {"ok": False, "error": {"code": code, "message": message}}, extra)

    def _authorized(self) -> bool:
        tok = self.node._token
        if not tok:
            return True
        got = self.headers.get("Authorization", "")
        if not got.startswith("Bearer "):
            return False
        return hmac.compare_digest(got[7:].encode("utf-8"), tok.encode("utf-8"))

    def _body(self) -> bytes | None:
        if self.headers.get("Transfer-Encoding"):
            self._err(501, "http.rejected", "Transfer-Encoding is not supported")
            return None
        cls = self.headers.get_all("Content-Length") or []
        if len(set(cls)) > 1:
            self._err(400, "http.rejected", "conflicting Content-Length")
            return None
        if not cls:
            if self.command == "POST":
                self._err(411, "http.rejected", "Content-Length required")
                return None
            return b""
        if not cls[0].isdigit():
            self._err(400, "http.rejected", "bad Content-Length")
            return None
        n = int(cls[0])
        if n > MAX_BODY:
            self._err(413, "http.rejected", "body too large")
            return None
        data = self.rfile.read(n)
        if len(data) != n:
            return None                    # client vanished
        return data

    # ---- dispatch ---------------------------------------------------------
    def _route(self, method: str) -> None:
        node = self.node
        path = self.path
        if len(str(self.headers)) > MAX_HEADER:
            self._err(431, "http.rejected", "headers too large")
            return
        if path == "/health":
            if method != "GET":
                self._err(405, "http.method", "use GET", {"Allow": "GET"})
                return
            self._send(200, node._health(False))
            return
        if path not in ("/peers", "/receive", "/register", "/inbox/next"):
            self._err(404, "http.not_found", "unknown path")
            return
        if not self._authorized():
            node._count("rejected_auth")
            code = "auth.denied" if self.headers.get("Authorization") else "auth.required"
            self._err(401, code, "this node requires its shared token (Authorization: Bearer)",
                      {"WWW-Authenticate": "Bearer"})
            return
        if path == "/peers":
            if method != "GET":
                self._err(405, "http.method", "use GET", {"Allow": "GET"})
                return
            self._send(200, {"ok": True, "node_id": node.node_id, "peers": node.peers()})
            return
        if method != "POST":
            self._err(405, "http.method", "use POST", {"Allow": "POST"})
            return
        body = self._body()
        if body is None:
            return
        try:
            obj = json.loads(body.decode("utf-8")) if body else None
        except (ValueError, UnicodeDecodeError):
            obj = None
        if path == "/receive":
            self._receive(obj)
        elif path == "/register":
            if (not isinstance(obj, dict) or not valid_id(obj.get("node_id"))
                    or not valid_endpoint(obj.get("endpoint"))):
                self._err(400, "request.malformed",
                          "need node_id ([A-Za-z0-9._-]{1,63}) and endpoint host:port")
                return
            try:
                node.register(obj["node_id"], obj["endpoint"])
            except PolycallError:
                self._err(409, "registry.full", "peer registry is full")
                return
            self._send(200, {"ok": True, "registered": obj["node_id"]})
        else:
            t = obj.get("timeout_ms", 0) if isinstance(obj, dict) else -1
            if not isinstance(t, int) or isinstance(t, bool) or not 0 <= t <= 30000:
                self._err(400, "request.malformed", "timeout_ms must be 0..30000")
                return
            m = node._take(t)
            if m is None:
                self._send(200, {"ok": True, "message": None})
            else:
                self._send(200, {"ok": True, "message": {
                    "from": m.sender, "id": m.message_id,
                    "payload_b64": base64.b64encode(m.payload).decode("ascii")}})

    def _receive(self, obj: object) -> None:
        node = self.node
        if not isinstance(obj, dict):
            node._count("rejected_malformed")
            self._err(400, "request.malformed", "body is not a JSON object")
            return
        if obj.get("v") != 1 or isinstance(obj.get("v"), bool):
            node._count("rejected_malformed")
            self._err(400, "protocol.version", 'expected "v":1')
            return
        frm, mid, b64 = obj.get("from"), obj.get("id"), obj.get("payload_b64")
        if not valid_id(frm) or not valid_id(mid) or not isinstance(b64, str):
            node._count("rejected_malformed")
            self._err(400, "request.malformed",
                      "need string from, id ([A-Za-z0-9._-]{1,63}) and payload_b64")
            return
        try:
            data = base64.b64decode(b64, validate=True)
            if base64.b64encode(data).decode("ascii") != b64:
                raise binascii.Error("non-canonical")
        except (binascii.Error, ValueError):
            node._count("rejected_malformed")
            self._err(400, "request.malformed", "payload_b64 is not valid base64")
            return
        if len(data) > MAX_PAYLOAD:
            node._count("rejected_malformed")
            self._err(413, "payload.too_large", "payload exceeds 1 MiB")
            return
        status = node._deliver(frm, mid, data)
        if status == "busy":
            self._err(503, "inbox.full", "receiver inbox is full; retry later with the same id",
                      {"Retry-After": "1"})
            return
        self._send(200, {"ok": True, "status": "received", "node_id": node.node_id,
                         "id": mid, "duplicate": status == "duplicate"})

    def do_GET(self) -> None:  # noqa: N802
        self._route("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._route("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._route("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._route("DELETE")


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False      # never share a listening port

    def __init__(self, addr, handler, node: "FallbackPeer") -> None:
        self._slots = threading.BoundedSemaphore(MAX_CONNS)
        self.node = node
        super().__init__(addr, handler)

    def process_request(self, request, client_address):  # bounded concurrency
        if not self._slots.acquire(blocking=False):
            self.node._count("rejected_busy")
            try:
                body = b'{"ok":false,"error":{"code":"server.busy","message":"too many concurrent connections; retry later"}}'
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json\r\n"
                                b"Retry-After: 1\r\nConnection: close\r\nContent-Length: "
                                + str(len(body)).encode() + b"\r\n\r\n" + body)
            finally:
                self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class FallbackPeer:
    """A polycall-peer/1 node implemented in Python (same API as Peer)."""

    backend = "python"

    def __init__(self, node_id: str, bind: str | None = "127.0.0.1:0",
                 token: str | None = None) -> None:
        if not valid_id(node_id):
            raise PolycallError(E_INVALID_ARGUMENT, "node_id must be 1-63 characters of [A-Za-z0-9._-]")
        self.node_id = node_id
        self._token = token or ""
        self._mu = threading.Condition()
        self._registry: dict[str, str] = {}
        self._inbox: collections.deque[_Message] = collections.deque()
        self._inbox_bytes = 0
        self._dedup: collections.deque[tuple[str, str]] = collections.deque(maxlen=DEDUP)
        self._dedup_set: set[tuple[str, str]] = set()
        self._cancel_gen = 0
        self._closed = False
        self._started = time.monotonic()
        self._counters = collections.Counter()
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self.endpoint = ""
        if bind is not None:
            if not valid_endpoint(bind, allow_zero=True):
                raise PolycallError(E_INVALID_ARGUMENT, f"bind endpoint {bind!r} is not host:port")
            host, port = split_endpoint(bind)
            if not _loopback(host) and not self._token:
                raise PolycallError(E_CONFIG, f"refusing to listen on non-loopback {host!r} without a token")
            handler = type("Handler", (_Handler,), {"node": self})
            try:
                self._server = _Server((host, port), handler, self)
            except OSError as exc:
                from .errors import E_ADDRESS_IN_USE, E_PERMISSION
                code = (E_ADDRESS_IN_USE if exc.errno in (98, 10048)
                        else E_PERMISSION if exc.errno in (13, 10013) else E_TRANSPORT)
                raise PolycallError(code, f"cannot listen on {bind}: {exc}") from None
            self.endpoint = f"{host}:{self._server.server_address[1]}"
            self._thread = threading.Thread(target=self._server.serve_forever,
                                            kwargs={"poll_interval": 0.2},
                                            name=f"polycall-peer-{node_id}", daemon=True)
            self._thread.start()

    # ---- node state ---------------------------------------------------------
    def _check(self) -> None:
        if self._closed:
            raise PolycallError(E_INVALID_HANDLE, "peer is closed")

    def _count(self, what: str) -> None:
        with self._mu:
            self._counters[what] += 1

    def _deliver(self, frm: str, mid: str, data: bytes) -> str:
        with self._mu:
            key = (frm, mid)
            if key in self._dedup_set:
                self._counters["duplicates"] += 1
                return "duplicate"
            if len(self._inbox) >= INBOX_MAX or self._inbox_bytes + len(data) > INBOX_BYTES:
                self._counters["rejected_busy"] += 1
                return "busy"
            self._inbox.append(_Message(frm, mid, data))
            self._inbox_bytes += len(data)
            if len(self._dedup) == self._dedup.maxlen:
                self._dedup_set.discard(self._dedup[0])
            self._dedup.append(key)
            self._dedup_set.add(key)
            self._counters["received"] += 1
            self._mu.notify_all()
            return "stored"

    def _take(self, timeout_ms: int | None, gen0: int | None = None) -> _Message | None:
        deadline = None if timeout_ms is None else time.monotonic() + timeout_ms / 1000.0
        with self._mu:
            if gen0 is None:
                gen0 = self._cancel_gen
            while True:
                if self._closed:
                    raise PolycallError(E_CLOSED, "peer node closed while waiting")
                if self._cancel_gen != gen0:
                    raise PolycallError(E_CANCELLED, "receive was cancelled")
                if self._inbox:
                    m = self._inbox.popleft()
                    self._inbox_bytes -= len(m.payload)
                    return m
                if deadline is not None:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        return None
                    self._mu.wait(left)
                else:
                    self._mu.wait()

    def _health(self, with_registry: bool) -> dict:
        with self._mu:
            h = {"ok": True, "protocol": PROTOCOL, "node_id": self.node_id,
                 "endpoint": self.endpoint, "status": "stopping" if self._closed else "ok",
                 "peers": len(self._registry), "inbox": len(self._inbox),
                 "inbox_capacity": INBOX_MAX,
                 "received": self._counters["received"], "duplicates": self._counters["duplicates"],
                 "rejected_busy": self._counters["rejected_busy"],
                 "rejected_malformed": self._counters["rejected_malformed"],
                 "rejected_auth": self._counters["rejected_auth"],
                 "sent_ok": self._counters["sent_ok"], "sent_failed": self._counters["sent_failed"],
                 "uptime_ms": int((time.monotonic() - self._started) * 1000),
                 "auth": bool(self._token), "implementation": "pypolycall fallback (pure Python)"}
            if with_registry:
                h["registry"] = dict(self._registry)
            return h

    # ---- public API (same shape as Peer) ------------------------------------
    def register(self, peer_id: str, endpoint: str) -> None:
        self._check()
        if not valid_id(peer_id):
            raise PolycallError(E_INVALID_ARGUMENT, "peer_id must be 1-63 characters of [A-Za-z0-9._-]")
        if not valid_endpoint(endpoint):
            raise PolycallError(E_INVALID_ARGUMENT, "endpoint must be host:port (port 1-65535)")
        with self._mu:
            if peer_id not in self._registry and len(self._registry) >= 64:
                raise PolycallError(E_TOO_LARGE, "peer registry is full (max 64)")
            self._registry[peer_id] = endpoint

    def unregister(self, peer_id: str) -> None:
        self._check()
        with self._mu:
            if self._registry.pop(peer_id, None) is None:
                raise PolycallError(E_NOT_FOUND, f"peer {peer_id!r} is not registered")

    def peers(self) -> dict[str, str]:
        self._check()
        with self._mu:
            return dict(self._registry)

    def health(self) -> dict:
        self._check()
        return self._health(True)

    def _resolve(self, peer: str) -> tuple[str, int, str | None]:
        with self._mu:
            ep = self._registry.get(peer)
        expected = peer if ep else None
        if ep is None:
            if ":" not in peer:
                raise PolycallError(E_NOT_FOUND, f"peer {peer!r} is not registered on node {self.node_id!r}")
            ep = peer
        if not valid_endpoint(ep):
            raise PolycallError(E_INVALID_ARGUMENT, f"{ep!r} is not host:port")
        host, port = split_endpoint(ep)
        return host, port, expected

    def _exchange(self, host: str, port: int, method: str, path: str, body: bytes | None,
                  timeout_ms: int) -> tuple[int, dict | None]:
        conn = http.client.HTTPConnection(host, port, timeout=max(timeout_ms, 1) / 1000.0)
        headers = {"Connection": "close", "Accept": "application/json",
                   "User-Agent": "pypolycall-fallback/1"}
        if self._token:
            headers["Authorization"] = "Bearer " + self._token
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            raw = resp.read(64 * 1024 + 1)
            status = resp.status
        except socket.timeout:
            raise PolycallError(E_TIMEOUT, f"{host}:{port} did not answer within {timeout_ms} ms") from None
        except (ConnectionError, OSError) as exc:
            if isinstance(exc, TimeoutError):
                raise PolycallError(E_TIMEOUT, f"{host}:{port} did not answer within {timeout_ms} ms") from None
            raise PolycallError(E_TRANSPORT, f"{host}:{port}: {exc}") from None
        except http.client.HTTPException as exc:
            raise PolycallError(E_PROTOCOL, f"{host}:{port} did not answer with HTTP: {exc}") from None
        finally:
            conn.close()
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            obj = None
        return status, obj if isinstance(obj, dict) else None

    def ping(self, peer: str, timeout_ms: int = 3000) -> None:
        self._check()
        host, port, expected = self._resolve(peer)
        status, obj = self._exchange(host, port, "GET", "/health", None, timeout_ms)
        if status != 200 or not obj or not isinstance(obj.get("node_id"), str):
            raise PolycallError(E_PROTOCOL, f"{host}:{port} is not a healthy polycall peer (HTTP {status})")
        if expected and obj["node_id"] != expected:
            raise PolycallError(E_PROTOCOL, f"{host}:{port} answers as {obj['node_id']!r}, expected {expected!r}")
        if obj.get("status") != "ok":
            raise PolycallError(E_BUSY, f"peer reports status {obj.get('status')!r}")

    def send(self, peer: str, payload: bytes | str, message_id: str | None = None,
             timeout_ms: int = 5000) -> str:
        self._check()
        data = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
        if len(data) > MAX_PAYLOAD:
            raise PolycallError(E_TOO_LARGE, f"payload of {len(data)} bytes exceeds the 1 MiB limit")
        mid = message_id or uuid.uuid4().hex
        if not valid_id(mid):
            raise PolycallError(E_INVALID_ARGUMENT, "message_id must be 1-63 characters of [A-Za-z0-9._-]")
        try:
            host, port, expected = self._resolve(peer)
            body = json.dumps({"v": 1, "id": mid, "from": self.node_id,
                               "payload_b64": base64.b64encode(data).decode("ascii")},
                              separators=(",", ":")).encode("utf-8")
            status, obj = self._exchange(host, port, "POST", "/receive", body, timeout_ms)
            if status != 200:
                code = {401: E_AUTH, 413: E_TOO_LARGE, 503: E_BUSY, 400: E_REMOTE,
                        404: E_PROTOCOL, 405: E_PROTOCOL}.get(status, E_REMOTE)
                ecode = (obj or {}).get("error", {}).get("code", "") if obj else ""
                raise PolycallError(code, f"{host}:{port} refused {mid!r} (HTTP {status} {ecode})")
            if not obj or obj.get("ok") is not True or obj.get("id") != mid or not isinstance(obj.get("node_id"), str):
                raise PolycallError(E_PROTOCOL, f"{host}:{port} answered 200 without acknowledging {mid!r}")
            if expected and obj["node_id"] != expected:
                raise PolycallError(E_PROTOCOL, f"{mid!r} was acknowledged by {obj['node_id']!r}, expected {expected!r}")
        except PolycallError:
            self._count("sent_failed")
            raise
        self._count("sent_ok")
        return mid

    def recv(self, timeout_ms: int | None = 5000) -> tuple[str, str, bytes]:
        """Return (sender, message_id, payload). timeout_ms None waits forever."""
        self._check()
        m = self._take(timeout_ms)
        if m is None:
            raise PolycallError(E_TIMEOUT, f"no message within {timeout_ms} ms")
        return m.sender, m.message_id, m.payload

    def cancel(self) -> None:
        self._check()
        with self._mu:
            self._cancel_gen += 1
            self._mu.notify_all()

    def close(self) -> None:
        if self._closed:
            raise PolycallError(E_INVALID_HANDLE, "peer is already closed")
        with self._mu:
            self._closed = True
            self._mu.notify_all()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._thread.join(5)

    def __enter__(self) -> "FallbackPeer":
        return self

    def __exit__(self, *exc: object) -> None:
        if not self._closed:
            self.close()
