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


def test_errors_carry_code_strerror_and_detail(native, tmp_path):
    missing = tmp_path / "nope-pypolycallrc"
    with pytest.raises(PolycallError) as ei:
        pypolycall.run_config(str(missing))
    e = ei.value
    assert e.code == E.E_NOT_FOUND == -7
    assert e.strerror == pypolycall.strerror(E.E_NOT_FOUND)       # polycall_strerror()
    assert e.strerror.startswith("POLYCALL_E_NOT_FOUND:") and e.name == "POLYCALL_E_NOT_FOUND"
    assert "nope-pypolycallrc" in e.detail                         # polycall_last_error()
    assert "POLYCALL_E_NOT_FOUND" in str(e) and "nope-pypolycallrc" in str(e)
    assert pypolycall.strerror(-999).startswith("POLYCALL_E_UNKNOWN")
    assert pypolycall.strerror(0).startswith("POLYCALL_OK")


def test_unicode_config_path(native, tmp_path):
    """A non-ASCII (UTF-8) config path reaches the core intact -- on Windows
    the core opens it with _wfopen since 58bae1b."""
    d = tmp_path / "pÿ-конфиг-世界"
    d.mkdir()
    rc = d / "pypolycallrc-é"
    rc.write_text("log_level=info\nmax_connections=10\n", encoding="utf-8")
    assert pypolycall.run_config(str(rc)) == 0
    assert pypolycall.run_config(str(rc), strict=False) == 0
    import json
    assert json.loads(pypolycall.describe(str(rc)))["values"] == {
        "log_level": "info", "max_connections": "10"}
    with pytest.raises(PolycallError) as ei:
        pypolycall.run_config(str(d / "missing-ü"))
    assert ei.value.code == E.E_NOT_FOUND and "missing-ü" in ei.value.detail


def test_strings_with_nul_are_rejected_not_truncated(native, tmp_path):
    rc = tmp_path / "pypolycallrc"
    rc.write_text("log_level=info\n")
    with pytest.raises(PolycallError) as ei:
        pypolycall.run_config(str(rc) + "\x00ignored")
    assert ei.value.code == E.E_INVALID_ARGUMENT
    with pytest.raises(PolycallError) as ei:
        pypolycall.Peer("ab\x00cd")
    assert ei.value.code == E.E_INVALID_ARGUMENT and "NUL" in ei.value.detail


def test_integer_boundaries(native):
    with pypolycall.Peer("bound-a") as a, pypolycall.Peer("bound-b") as b:
        # timeouts cross the ABI as uint32_t: out-of-range values are refused
        # in Python instead of being silently truncated by ctypes
        for bad in (-1, 1 << 32, (1 << 32) + 5000):
            with pytest.raises(PolycallError) as ei:
                b.recv(bad)
            assert ei.value.code == E.E_INVALID_ARGUMENT, bad
            with pytest.raises(PolycallError) as ei:
                a.send(b.endpoint, b"x", timeout_ms=bad)
            assert ei.value.code == E.E_INVALID_ARGUMENT, bad
        with pytest.raises(PolycallError) as ei:
            b.recv(1.5)
        assert ei.value.code == E.E_INVALID_ARGUMENT
        t0 = time.monotonic()
        with pytest.raises(PolycallError) as ei:
            b.recv(0)                                  # 0 = poll
        assert ei.value.code == E.E_TIMEOUT and time.monotonic() - t0 < 1
        # payload limits: exactly 1 MiB arrives intact through a fixed
        # buffer of exactly 1 MiB; 1 MiB + 1 is refused before any I/O
        big = bytes(i % 253 for i in range(1 << 20))
        a.send(b.endpoint, big, "exact-1mib", timeout_ms=20000)
        assert b.recv(20000, capacity=1 << 20) == ("bound-a", "exact-1mib", big)
        with pytest.raises(PolycallError) as ei:
            a.send(b.endpoint, big + b"!", "over-1mib")
        assert ei.value.code == E.E_TOO_LARGE
        with pytest.raises(PolycallError) as ei:
            b.recv(300)
        assert ei.value.code == E.E_TIMEOUT
        # call(): the library accepts 1..600000 ms; Python refuses what
        # uint32_t cannot hold rather than wrapping it into range
        for bad in (0, 600001):
            with pytest.raises(PolycallError) as ei:
                pypolycall.call("127.0.0.1:1", "debug", "echo", None, bad)
            assert ei.value.code == E.E_INVALID_ARGUMENT, bad
        for bad in (-1, (1 << 32) + 1000):
            with pytest.raises(PolycallError) as ei:
                pypolycall.call("127.0.0.1:1", "debug", "echo", None, bad)
            assert ei.value.code == E.E_INVALID_ARGUMENT and "4294967295" in ei.value.detail


def test_too_small_buffer_leaves_message_queued(pair):
    a, b = pair
    a.send("beta", b"z" * 100, "big-100")
    with pytest.raises(PolycallError) as ei:
        b.recv(5000, capacity=10)
    assert ei.value.code == E.E_TOO_LARGE and ei.value.needed == 100
    with pytest.raises(PolycallError) as ei:
        b.recv(0, capacity=0)
    assert ei.value.code == E.E_TOO_LARGE and ei.value.needed == 100
    assert b.recv(0, capacity=100) == ("alpha", "big-100", b"z" * 100)


def test_node_id_endpoint_and_send_only_node(native, token):
    with pypolycall.Peer("ident-1", token=token) as p, pypolycall.Peer("sender-only", None, token) as s:
        assert p.node_id == "ident-1" and p.endpoint.startswith("127.0.0.1:")
        assert s.endpoint == ""
        mid = s.send(p.endpoint, b"from a send-only node")
        assert p.recv(3000) == ("sender-only", mid, b"from a send-only node")
        assert p.health()["node_id"] == "ident-1"


def test_blocking_recv_does_not_block_other_threads(native, token):
    """ctypes drops the GIL inside polycall_peer_recv: while one thread is
    blocked in recv(None), other threads keep running Python code and
    making library calls; cancel() and close() then wake the blocked call."""
    waiter = pypolycall.Peer("blocked", token=token)
    outcome = {}

    def blocked():
        t0 = time.monotonic()
        try:
            waiter.recv(None)
        except PolycallError as exc:
            outcome["code"], outcome["after"] = exc.code, time.monotonic() - t0

    t = threading.Thread(target=blocked)
    t.start()
    time.sleep(0.2)
    assert t.is_alive()
    ticks = 0
    t0 = time.monotonic()
    while time.monotonic() - t0 < 0.3:            # pure-Python work progresses
        ticks += 1
    assert ticks > 1000
    with pypolycall.Peer("busy-a", token=token) as a, pypolycall.Peer("busy-b", token=token) as b:
        t1 = time.monotonic()
        for i in range(20):                       # library calls from this thread
            a.send(b.endpoint, f"m{i}".encode(), f"m{i}")
            assert b.recv(3000)[2] == f"m{i}".encode()
        assert time.monotonic() - t1 < 10
    assert t.is_alive() and "code" not in outcome  # still blocked, untouched
    waiter.cancel()
    t.join(5)
    assert not t.is_alive() and outcome["code"] == E.E_CANCELLED
    outcome.clear()
    t = threading.Thread(target=blocked)
    t.start()
    time.sleep(0.2)
    waiter.close()
    t.join(5)
    assert not t.is_alive() and outcome["code"] == E.E_CLOSED


def test_concurrent_calls_on_one_node_from_many_threads(native, token):
    """All ABI functions are thread-safe: several threads use ONE handle."""
    with pypolycall.Peer("shared-tx", None, token) as tx, pypolycall.Peer("shared-rx", token=token) as rx:
        errors = []

        def worker(n):
            try:
                for i in range(15):
                    tx.send(rx.endpoint, f"{n}:{i}".encode(), f"s{n}-{i}")
                    tx.health()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
        for th in threads:
            th.start()
        got = {rx.recv(10000)[1] for _ in range(90)}
        for th in threads:
            th.join(30)
        assert not errors and len(got) == 90
