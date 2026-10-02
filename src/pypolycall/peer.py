"""Peer node over the native libpolycall (polycall_peer_* in polycall.h)."""
from __future__ import annotations

import ctypes
import json
import threading
import uuid

from . import _native
from .errors import E_INVALID_HANDLE, E_TIMEOUT, E_TOO_LARGE, PolycallError

UINT32_MAX = 0xFFFFFFFF


class Peer:
    """A polycall-peer/1 node owned by this process.

    The node's registry and inbox are its own: nothing is shared with any
    other node (in this process or another) unless that node is told
    explicitly via register(). Thread-safe; recv() may block in one thread
    while other threads send, cancel or close.
    """

    backend = "native"

    def __init__(self, node_id: str, bind: str | None = "127.0.0.1:0",
                 token: str | None = None) -> None:
        self._lib = _native.load()
        h = ctypes.c_int32(0)
        rc = self._lib.polycall_peer_open(_native.enc(node_id), _native.enc(bind),
                                          _native.enc(token), ctypes.byref(h))
        _native.check(self._lib, rc)
        self._h = h.value
        self._closed = False
        self._close_lock = threading.Lock()
        self.node_id = node_id
        buf = ctypes.create_string_buffer(128)
        _native.check(self._lib, self._lib.polycall_peer_endpoint(self._h, buf, len(buf)))
        self.endpoint = buf.value.decode("utf-8")

    @property
    def handle(self) -> int:
        return self._h

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
            self._h, _native.enc(peer_id), _native.enc(endpoint)))

    def unregister(self, peer_id: str) -> None:
        _native.check(self._lib, self._lib.polycall_peer_unregister(self._h, _native.enc(peer_id)))

    def peers(self) -> dict:
        return json.loads(self._text(self._lib.polycall_peer_list))

    def health(self) -> dict:
        return json.loads(self._text(self._lib.polycall_peer_health))

    def ping(self, peer: str, timeout_ms: int = 3000) -> None:
        _native.check(self._lib, self._lib.polycall_peer_ping(self._h, _native.enc(peer), timeout_ms))

    def send(self, peer: str, payload: bytes | str, message_id: str | None = None,
             timeout_ms: int = 5000) -> str:
        """Deliver payload; returns the message id. One attempt -- on
        PolycallError with E_TIMEOUT the outcome is unknown: retry with the
        returned/same id and the receiver keeps exactly one copy."""
        data = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
        mid = message_id or uuid.uuid4().hex
        rc = self._lib.polycall_peer_send(self._h, _native.enc(peer), data, len(data),
                                          _native.enc(mid), timeout_ms)
        _native.check(self._lib, rc)
        return mid

    def recv(self, timeout_ms: int | None = 5000) -> tuple[str, str, bytes]:
        """Return (sender, message_id, payload). None waits forever (until a
        message, cancel() or close())."""
        t = UINT32_MAX if timeout_ms is None else int(timeout_ms)
        sender = ctypes.create_string_buffer(64)
        mid = ctypes.create_string_buffer(64)
        cap = 64 * 1024
        while True:
            buf = ctypes.create_string_buffer(cap)
            n = ctypes.c_size_t(0)
            rc = self._lib.polycall_peer_recv(self._h, t, sender, 64, mid, 64, buf, cap,
                                              ctypes.byref(n))
            if rc == E_TOO_LARGE and n.value > cap:
                cap = n.value          # message stays queued: fetch it at once
                t = 0
                continue
            if rc == E_TIMEOUT:
                raise PolycallError(rc, _native.last_error(self._lib))
            _native.check(self._lib, rc)
            return (sender.value.decode("utf-8"), mid.value.decode("utf-8"),
                    ctypes.string_at(buf, n.value))

    def cancel(self) -> None:
        _native.check(self._lib, self._lib.polycall_peer_cancel(self._h))

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                raise PolycallError(E_INVALID_HANDLE, "peer is already closed")
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
