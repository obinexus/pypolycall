"""The pure-Python polycall-peer/1 fallback: on its own, and interoperating
with the native C node in both directions (same wire protocol)."""
from __future__ import annotations

import socket

import pytest

import pypolycall
from pypolycall import FallbackPeer, PolycallError
from pypolycall import errors as E


def test_python_to_python(token):
    with FallbackPeer("py-a", token=token) as a, FallbackPeer("py-b", token=token) as b:
        a.register("py-b", b.endpoint)
        mid = a.send("py-b", bytes(range(256)))
        assert b.recv(3000) == ("py-a", mid, bytes(range(256)))
        a.send("py-b", b"dup", "d1")
        a.send("py-b", b"dup", "d1")
        assert b.recv(3000) == ("py-a", "d1", b"dup")
        with pytest.raises(PolycallError) as ei:
            b.recv(200)
        assert ei.value.code == E.E_TIMEOUT
        assert b.peers() == {}


@pytest.mark.parametrize("direction", ["python->native", "native->python"])
def test_fallback_native_interop(native, token, direction):
    with FallbackPeer("py-node", token=token) as py, pypolycall.Peer("c-node", token=token) as c:
        py.register("c-node", c.endpoint)
        c.register("py-node", py.endpoint)
        data = "interop ✓".encode("utf-8") + b"\x00\x01"
        if direction == "python->native":
            mid = py.send("c-node", data)
            assert c.recv(3000) == ("py-node", mid, data)
            c.ping("py-node")
        else:
            mid = c.send("py-node", data)
            assert py.recv(3000) == ("c-node", mid, data)
            py.ping("c-node")


def test_fallback_auth_and_identity(token):
    with FallbackPeer("guarded", token=token) as g, FallbackPeer("outsider") as o:
        with pytest.raises(PolycallError) as ei:
            o.send(g.endpoint, b"x")
        assert ei.value.code == E.E_AUTH
        o.register("someone-else", g.endpoint)
        with pytest.raises(PolycallError) as ei:
            o.ping("someone-else")
        assert ei.value.code == E.E_PROTOCOL


def _raw(endpoint: str, data: bytes) -> bytes:
    host, port = endpoint.rsplit(":", 1)
    with socket.create_connection((host, int(port)), timeout=5) as s:
        s.sendall(data)
        out = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                return out
            out += chunk


@pytest.mark.parametrize("request_bytes,status", [
    (b"POST /receive HTTP/1.1\r\nTransfer-Encoding: chunked\r\nAuthorization: Bearer T\r\n\r\n0\r\n\r\n", b"501"),
    (b"POST /receive HTTP/1.1\r\nAuthorization: Bearer T\r\n\r\n", b"411"),
    (b"POST /receive HTTP/1.1\r\nAuthorization: Bearer T\r\nContent-Length: 9\r\n\r\n{\"v\":1,}x", b"400"),
    (b"POST /nowhere HTTP/1.1\r\nContent-Length: 0\r\n\r\n", b"404"),
    (b"DELETE /peers HTTP/1.1\r\nAuthorization: Bearer T\r\n\r\n", b"405"),
    (b"POST /receive HTTP/1.1\r\nAuthorization: Bearer T\r\nContent-Length: 99999999\r\n\r\n", b"413"),
])
def test_fallback_rejects_malformed_http(monkeypatch, request_bytes, status):
    with FallbackPeer("strict", token="T") as p:
        resp = _raw(p.endpoint, request_bytes)
        assert resp.split(b" ", 2)[1] == status, resp[:80]
        assert p.health()["status"] == "ok"


def test_auto_backend_prefers_native(native):
    with pypolycall.open_peer("auto-node") as p:
        assert p.backend == "native"
