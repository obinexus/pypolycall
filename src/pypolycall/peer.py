"""Peer node over the native libpolycall (polycall_peer_* in polycall.h)."""
from __future__ import annotations

import ctypes
import json
import threading
import uuid

from . import _native
from .errors import (E_INVALID_ARGUMENT, E_INVALID_HANDLE, E_TOO_LARGE, UINT32_MAX,
                     PolycallError, check_u32)

PEER_ID_MAX = 64            # POLYCALL_PEER_ID_MAX / POLYCALL_MESSAGE_ID_MAX (incl. NUL)
ENDPOINT_MAX = 128          # POLYCALL_ENDPOINT_MAX
MAX_PAYLOAD = 1 << 20       # POLYCALL_PEER_MAX_PAYLOAD


class Peer:
    """A polycall-peer/1 node owned by this process.

    The node's registry and inbox are its own: nothing is shared with any
    other node (in this process or another) unless that node is told
    explicitly via register(). Thread-safe; recv() may block in one thread
    while other threads send, cancel or close (ctypes releases the GIL for
    the duration of every library call).
    """

    backend = "native"

    def __init__(self, node_id: str, bind: str | None = "127.0.0.1:0",
                 token: str | None = None) -> None:
        self._lib = _native.load()
        self._closed = True               # until the handle exists (see __del__)
        h = ctypes.c_int32(0)
        rc = self._lib.polycall_peer_open(_native.enc(node_id, "node_id"),
                                          _native.enc(bind, "bind endpoint"),
                                          _native.enc(token, "token"), ctypes.byref(h))
        _native.check(self._lib, rc)
        self._h = h.value
        self._closed = False
        self._close_lock = threading.Lock()
        self.node_id = self._small(self._lib.polycall_peer_node_id, PEER_ID_MAX)
        self.endpoint = self._small(self._lib.polycall_peer_endpoint, ENDPOINT_MAX)

    @property
    def handle(self) -> int:
        return self._h

    def _small(self, fn, cap: int) -> str:
        buf = ctypes.create_string_buffer(cap)
        rc = fn(self._h, buf, cap)
        if rc < 0:
            raise _native.error(self._lib, rc)
        return buf.value.decode("utf-8")

    def _text(self, fn, *args) -> str:
        cap = 4096
        while True:
            buf = ctypes.create_string_buffer(cap)
            n = ctypes.c_size_t(0)
            rc = fn(self._h, *args, buf, cap, ctypes.byref(n))
            if rc == E_TOO_LARGE and n.value + 1 > cap:
                cap = n.value + 1
                continue
            _native.check(self._lib, rc)
            return buf.value.decode("utf-8")

    def register(self, peer_id: str, endpoint: str) -> None:
        _native.check(self._lib, self._lib.polycall_peer_register(
            self._h, _native.enc(peer_id, "peer_id"), _native.enc(endpoint, "endpoint")))

    def unregister(self, peer_id: str) -> None:
        _native.check(self._lib, self._lib.polycall_peer_unregister(
            self._h, _native.enc(peer_id, "peer_id")))

    def peers(self) -> dict:
        """THIS node's registry {peer_id: "host:port"} (polycall_peer_list)."""
        return json.loads(self._text(self._lib.polycall_peer_list))

    def health(self) -> dict:
        return json.loads(self._text(self._lib.polycall_peer_health))

    def ping(self, peer: str, timeout_ms: int = 3000) -> None:
        t = check_u32(timeout_ms, "timeout_ms")
        _native.check(self._lib, self._lib.polycall_peer_ping(
            self._h, _native.enc(peer, "peer"), t))

    def send(self, peer: str, payload: bytes | bytearray | memoryview | str,
             message_id: str | None = None, timeout_ms: int = 5000) -> str:
        """Deliver payload; returns the message id. One attempt -- on
        PolycallError with E_TIMEOUT the outcome is unknown: retry with the
        returned/same id and the receiver keeps exactly one copy."""
        t = check_u32(timeout_ms, "timeout_ms")
        data = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
        mid = message_id or uuid.uuid4().hex
        rc = self._lib.polycall_peer_send(self._h, _native.enc(peer, "peer"), data, len(data),
                                          _native.enc(mid, "message_id"), t)
        _native.check(self._lib, rc)
        return mid

    def recv(self, timeout_ms: int | None = 5000,
             capacity: int | None = None) -> tuple[str, str, bytes]:
        """Return (sender, message_id, payload).

        timeout_ms: 0 polls, None waits until a message, cancel() (E_CANCELLED)
        or close() (E_CLOSED); otherwise 1..4294967294 ms (E_TIMEOUT).
        capacity: None sizes the buffer for whatever arrives; an int is a
        fixed payload buffer -- a larger message raises E_TOO_LARGE with
        ``.needed`` set, and stays queued for the next recv().
        """
        t = UINT32_MAX if timeout_ms is None else check_u32(timeout_ms, "timeout_ms")
        if capacity is not None:
            if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 0:
                raise PolycallError(E_INVALID_ARGUMENT, "capacity must be an int >= 0")
        sender = ctypes.create_string_buffer(PEER_ID_MAX)
        mid = ctypes.create_string_buffer(PEER_ID_MAX)
        cap = capacity if capacity is not None else 64 * 1024
        while True:
            buf = ctypes.create_string_buffer(max(cap, 1))
            n = ctypes.c_size_t(0)
            rc = self._lib.polycall_peer_recv(self._h, t, sender, PEER_ID_MAX, mid, PEER_ID_MAX,
                                              buf, cap, ctypes.byref(n))
            if rc == E_TOO_LARGE and n.value > cap:
                if capacity is not None:   # caller's fixed buffer: message stays queued
                    raise _native.error(self._lib, rc, needed=n.value)
                cap = n.value              # message stays queued: fetch it at once
                t = 0
                continue
            _native.check(self._lib, rc)
            return (sender.value.decode("utf-8"), mid.value.decode("utf-8"),
                    ctypes.string_at(buf, n.value))

    def cancel(self) -> None:
        _native.check(self._lib, self._lib.polycall_peer_cancel(self._h))

    def close(self) -> None:
        """Stop the node, waking blocked receivers with E_CLOSED. A second
        close raises E_INVALID_HANDLE (as the library does)."""
        with self._close_lock:
            if self._closed:
                raise PolycallError(E_INVALID_HANDLE, "peer is already closed",
                                    strerror=_native.strerror(self._lib, E_INVALID_HANDLE))
            self._closed = True
        _native.check(self._lib, self._lib.polycall_peer_close(self._h))

    def __enter__(self) -> "Peer":
        return self

    def __exit__(self, *exc: object) -> None:
        if not self._closed:
            self.close()

    def __del__(self) -> None:          # last-resort cleanup, never raises
        try:
            if not getattr(self, "_closed", True):
                self._closed = True
                self._lib.polycall_peer_close(self._h)
        except Exception:
            pass
