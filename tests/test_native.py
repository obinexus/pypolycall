"""pypolycall against the REAL libpolycall (binding ABI v1)."""
from __future__ import annotations

import concurrent.futures
import threading
import time

import pytest

import pypolycall
from pypolycall import PolycallError
from pypolycall import errors as E


def test_version_and_abi(native):
    assert pypolycall.abi_version() == 1
    v = pypolycall.version()
    assert v.count(".") == 2 and int(v.split(".")[0]) >= 1
    assert pypolycall.library_path()


def test_run_config(native, tmp_path):
    rc = tmp_path / "pypolycallrc"
    rc.write_text("log_level=info\nmax_connections=1000\nnetwork_timeout=5000\ntls_enabled=false\n")
    pypolycall.run_config(str(rc))
    pypolycall.run_config(str(rc), strict=False)
    with pytest.raises(PolycallError) as ei:
        pypolycall.run_config(str(tmp_path / "missing"))
    assert ei.value.code == E.E_NOT_FOUND
    bad = tmp_path / "bad-polycallrc"
    bad.write_text("max_connections=many\n")
    with pytest.raises(PolycallError) as ei:
        pypolycall.run_config(str(bad))
    assert ei.value.code == E.E_CONFIG and "max_connections" in ei.value.detail
    unk = tmp_path / "unk-polycallrc"
    unk.write_text("log_level=info\nmystery=1\n")
    pypolycall.run_config(str(unk), strict=False)
    with pytest.raises(PolycallError) as ei:
        pypolycall.run_config(str(unk), strict=True)
    assert ei.value.code == E.E_CONFIG
    tls = tmp_path / "tls-polycallrc"
    tls.write_text("tls_enabled=true\ncert_file=/c.pem\nkey_file=/k.pem\n")
    with pytest.raises(PolycallError) as ei:
        pypolycall.run_config(str(tls))
    assert ei.value.code == E.E_UNSUPPORTED
    with pytest.raises(PolycallError) as ei:
        pypolycall.run_config("")
    assert ei.value.code == E.E_INVALID_ARGUMENT


def test_describe(native, tmp_path):
    pf = tmp_path / "Polycallfile"
    pf.write_text("server node 8080:8084\nnetwork start\nworkspace_root=/w\nlog_directory=/l\n"
                  "peer beta 127.0.0.1:9002\n")
    import json
    d = json.loads(pypolycall.describe(str(pf)))
    assert d["layer"] == "project" and d["peers"] == {"beta": "127.0.0.1:9002"}
    assert d["servers"][0]["host_port"] == 8080


@pytest.fixture
def pair(native, token):
    with pypolycall.Peer("alpha", token=token) as a, pypolycall.Peer("beta", token=token) as b:
        a.register("beta", b.endpoint)
        b.register("alpha", a.endpoint)
        yield a, b


def test_bidirectional_exact_payloads(pair):
    a, b = pair
    cases = [b"", "héllo — 世界 🌍".encode("utf-8"), bytes(range(256)),
             b"\x00" * 1000, bytes(i % 251 for i in range(1 << 20))]
    for i, data in enumerate(cases):
        mid = a.send("beta", data, f"a2b-{i}")
        frm, got_id, got = b.recv(5000)
        assert (frm, got_id, got) == ("alpha", mid, data)
    mid = b.send("alpha", b"pong")
    assert a.recv(3000) == ("beta", mid, b"pong")


def test_limits_and_arguments(pair):
    a, b = pair
    with pytest.raises(PolycallError) as ei:
        a.send("beta", b"x" * ((1 << 20) + 1))
    assert ei.value.code == E.E_TOO_LARGE
    with pytest.raises(PolycallError) as ei:
        a.send("beta", b"x", "bad id!")
    assert ei.value.code == E.E_INVALID_ARGUMENT
    with pytest.raises(PolycallError) as ei:
        a.send("nobody", b"x")
    assert ei.value.code == E.E_NOT_FOUND
    with pytest.raises(PolycallError) as ei:
        pypolycall.Peer("bad id")
    assert ei.value.code == E.E_INVALID_ARGUMENT
    with pytest.raises(PolycallError) as ei:
        pypolycall.Peer("x", "0.0.0.0:0")
    assert ei.value.code == E.E_CONFIG


def test_registry_ownership(native):
    with pypolycall.Peer("own-a") as a, pypolycall.Peer("own-b") as b:
        a.register("own-b", b.endpoint)
        assert a.peers() == {"own-b": b.endpoint}
        assert b.peers() == {}                       # never synchronised implicitly
        a.send("own-b", b"hi")
        assert b.peers() == {}                       # receiving does not register
        a.unregister("own-b")
        with pytest.raises(PolycallError) as ei:
            a.unregister("own-b")
        assert ei.value.code == E.E_NOT_FOUND


def test_duplicates_delivered_once(pair):
    a, b = pair
    a.send("beta", b"once", "dup-1")
    a.send("beta", b"once", "dup-1")                 # retry with the same id
    assert b.recv(3000) == ("alpha", "dup-1", b"once")
    with pytest.raises(PolycallError) as ei:
        b.recv(300)
    assert ei.value.code == E.E_TIMEOUT


def test_auth_and_dead_peer(native, token):
    with pypolycall.Peer("secured", token=token) as s, pypolycall.Peer("intruder", None) as i:
        with pytest.raises(PolycallError) as ei:
            i.send(s.endpoint, b"x")
        assert ei.value.code == E.E_AUTH
        dead = s.endpoint
    with pypolycall.Peer("survivor", token=token) as v:
        with pytest.raises(PolycallError) as ei:
            v.send(dead, b"x", timeout_ms=2000)
        assert ei.value.code == E.E_TRANSPORT
        assert v.health()["status"] == "ok"


def test_ping_identity(pair):
    a, b = pair
    a.ping("beta")
    a.register("impostor", b.endpoint)
    with pytest.raises(PolycallError) as ei:
        a.ping("impostor")
    assert ei.value.code == E.E_PROTOCOL


def test_timeout_cancel_close(native):
    p = pypolycall.Peer("waiter")
    t0 = time.monotonic()
    with pytest.raises(PolycallError) as ei:
        p.recv(150)
    assert ei.value.code == E.E_TIMEOUT and 0.1 <= time.monotonic() - t0 < 3
    result = {}

    def blocked():
        try:
            p.recv(None)
        except PolycallError as exc:
            result["code"] = exc.code

    t = threading.Thread(target=blocked)
    t.start()
    time.sleep(0.2)
    p.cancel()
    t.join(5)
    assert result["code"] == E.E_CANCELLED
    result.clear()                       # the close phase must set it afresh
    t = threading.Thread(target=blocked)
    t.start()
    time.sleep(0.2)
    p.close()
    t.join(5)
    assert result["code"] == E.E_CLOSED
    with pytest.raises(PolycallError) as ei:
        p.close()
    assert ei.value.code == E.E_INVALID_HANDLE
    with pytest.raises(PolycallError) as ei:
        p.send("127.0.0.1:1", b"x")
    assert ei.value.code == E.E_INVALID_HANDLE


def test_concurrent_senders(pair):
    a, b = pair

    def worker(n):
        return [a.send("beta", f"{n}-{i}".encode(), f"c{n}-{i}") for i in range(25)]

    got = set()
    with concurrent.futures.ThreadPoolExecutor(8) as ex:
        futs = [ex.submit(worker, n) for n in range(8)]
        while len(got) < 200:
            got.add(b.recv(5000)[1])
        sent = {m for f in futs for m in f.result()}
    assert got == sent and len(sent) == 200
