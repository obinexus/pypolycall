"""Cross-implementation interop with the C `polycall` CLI: separate OS
processes exchanging payloads both ways, and RPC calls into a runtime
started with `polycall start` and with `polycall daemon start`.

Only documented CLI arguments are used (`polycall help start|stop|peer|daemon`,
docs/CLI.md, docs/DAEMON.md); daemon settings come from Polycallfile keys."""
from __future__ import annotations

import base64
import concurrent.futures
import json
import os
import secrets
import subprocess
import sys

import pytest

import pypolycall
from pypolycall import PolycallError
from pypolycall import errors as E

from conftest import wait_file


def _readline_json(proc: subprocess.Popen) -> dict:
    line = proc.stdout.readline().decode("utf-8")
    return json.loads(line)


@pytest.mark.parametrize("backend", ["native", "python"])
def test_peer_with_c_cli_node_both_ways(cli, cli_proc, tmp_path, token, backend, request):
    if backend == "native":
        request.getfixturevalue("native")
    epf = tmp_path / "c.ep"
    c = cli_proc(cli, "peer", "serve", "--node-id", "c-cli", "--endpoint", "127.0.0.1:0",
                 "--endpoint-file", str(epf), "--print-messages")
    c_ep = wait_file(str(epf))
    assert _readline_json(c)["event"] == "listening"
    node = pypolycall.open_peer("py-" + backend, token=token, backend=backend)
    try:
        node.register("c-cli", c_ep)
        data = "from python ✓".encode("utf-8") + bytes([0, 255])
        mid = node.send("c-cli", data)
        msg = _readline_json(c)                       # printed by the C process
        assert msg["event"] == "message" and msg["from"] == "py-" + backend and msg["id"] == mid
        assert base64.b64decode(msg["payload_b64"]) == data
        r = subprocess.run([cli, "peer", "send", "--from", "c-cli", "--to", node.endpoint,
                            "--id", "c2py", "--payload", "from C"], capture_output=True, timeout=30)
        assert r.returncode == 0, r.stderr
        assert node.recv(5000) == ("c-cli", "c2py", b"from C")
        binf = tmp_path / "c2py.bin"
        binf.write_bytes(b"C\x00to\x00Python\xff\x00")
        r = subprocess.run([cli, "peer", "send", "--from", "c-cli", "--to", node.endpoint,
                            "--id", "c2py-bin", "--payload-file", str(binf)],
                           capture_output=True, timeout=30)
        assert r.returncode == 0, r.stderr
        assert node.recv(5000) == ("c-cli", "c2py-bin", b"C\x00to\x00Python\xff\x00")
    finally:
        node.close()


def test_python_peer_process_with_c_cli(native, cli, cli_proc, tmp_path, token):
    """`python -m pypolycall peer-serve` as its own OS process, fed by the C
    CLI's `peer send`; the reverse direction is read with `peer recv`."""
    epf = tmp_path / "py.ep"
    py = cli_proc(sys.executable, "-m", "pypolycall", "peer-serve", "--node-id", "py-proc",
                  "--backend", "native", "--endpoint-file", str(epf))
    py_ep = wait_file(str(epf))
    assert _readline_json(py)["event"] == "listening"
    payload = "C → Python process ✓".encode("utf-8")
    r = subprocess.run([cli, "peer", "send", "--from", "c-sender", "--to", py_ep, "--id", "c2pp",
                        "--payload", payload.decode("utf-8")], capture_output=True, timeout=30)
    assert r.returncode == 0, r.stderr
    msg = _readline_json(py)
    assert (msg["from"], msg["id"], base64.b64decode(msg["payload_b64"])) == ("c-sender", "c2pp", payload)

    cepf = tmp_path / "c.ep"
    cli_proc(cli, "peer", "serve", "--node-id", "c-inbox", "--endpoint", "127.0.0.1:0",
             "--endpoint-file", str(cepf))
    c_ep = wait_file(str(cepf))
    with pypolycall.Peer("py-sender", None, token) as s:
        s.send(c_ep, b"\x00\x01binary\xfe\xff", "pp2c")
    r = subprocess.run([cli, "peer", "recv", "--to", c_ep, "-t", "5000", "--raw"],
                       capture_output=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert r.stdout == b"\x00\x01binary\xfe\xff"


def test_call_into_runtime(native, cli, cli_proc, tmp_path):
    epf = tmp_path / "rt.ep"
    rt_token = "rt-" + secrets.token_hex(8)
    cli_proc(cli, "start", "--endpoint", "127.0.0.1:0", "--endpoint-file", str(epf),
             "--auth-token", rt_token)
    ep = wait_file(str(epf))
    out = json.loads(pypolycall.call(ep, "inventory", "get", '{"item_id":"widget-a"}'))
    assert out == {"item_id": "widget-a", "quantity": 42, "in_stock": True}
    nested = '{"a":[1,{"b":"x"}],"s":"' + "v" * 600 + '","u":"héllo ✓"}'
    assert json.loads(pypolycall.call(ep, "debug", "echo", nested)) == {"echo": json.loads(nested)}
    assert json.loads(pypolycall.call(ep, "debug", "echo")) == {"echo": None}   # NULL input
    with pytest.raises(PolycallError) as ei:
        pypolycall.call(ep, "inventory", "teleport", "{}")
    assert ei.value.code == E.E_NOT_FOUND and "operation.unknown" in ei.value.output
    with pytest.raises(PolycallError) as ei:
        pypolycall.call(ep, "inventory", "get", '{"item_id":"nope"}')
    assert ei.value.code == E.E_REMOTE and "item.unknown" in ei.value.output
    with pytest.raises(PolycallError) as ei:
        pypolycall.call(ep, "debug", "sleep", '{"ms":2000}', timeout_ms=100)
    assert ei.value.code == E.E_TIMEOUT
    with pytest.raises(PolycallError) as ei:
        pypolycall.call(ep, "debug", "echo", "{not json")
    assert ei.value.code == E.E_INVALID_ARGUMENT

    def one(i):
        return json.loads(pypolycall.call(ep, "debug", "echo", json.dumps({"i": i})))["echo"]["i"]

    with concurrent.futures.ThreadPoolExecutor(8) as ex:          # concurrent calls
        assert sorted(ex.map(one, range(64))) == list(range(64))

    r = subprocess.run([cli, "stop", "--endpoint", ep, "--auth-token", "wrong-" + rt_token],
                       capture_output=True, timeout=30)
    assert r.returncode == 7, r.stderr                            # refused, keeps serving
    assert json.loads(pypolycall.call(ep, "debug", "echo", "1")) == {"echo": 1}
    r = subprocess.run([cli, "stop", "--endpoint", ep, "--auth-token", rt_token],
                       capture_output=True, timeout=30)
    assert r.returncode == 0, r.stderr
    with pytest.raises(PolycallError) as ei:
        pypolycall.call(ep, "debug", "echo", None, 1000)
    assert ei.value.code == E.E_TRANSPORT                         # no runtime
    with pytest.raises(PolycallError) as ei:
        pypolycall.call("not-an-endpoint", "debug", "echo", None, 1000)
    assert ei.value.code == E.E_INVALID_ARGUMENT


def test_call_into_daemon(native, cli, tmp_path):
    """`polycall daemon start` (background, own state dir, ephemeral port
    from the documented Polycallfile keys), calls, authenticated stop."""
    work = tmp_path / "daemon"
    work.mkdir()
    pf = work / "Polycallfile"
    pf.write_text("daemon_endpoint=127.0.0.1:0\ndaemon_state_dir=state\n"
                  "auth_token_env=PYPOLYCALL_DAEMON_TOKEN\n", encoding="utf-8")
    env = {**os.environ, "PYPOLYCALL_DAEMON_TOKEN": "dt-" + secrets.token_hex(8)}
    r = subprocess.run([cli, "daemon", "start", str(pf)], capture_output=True, env=env, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    try:
        state = json.loads((work / "state" / "daemon.json").read_text(encoding="utf-8"))
        ep = state["endpoint"]
        assert state["auth"] is True and ep.startswith("127.0.0.1:")
        out = json.loads(pypolycall.call(ep, "inventory", "get", '{"item_id":"widget-b"}'))
        assert out == {"item_id": "widget-b", "quantity": 7, "in_stock": True}
        with pytest.raises(PolycallError) as ei:
            pypolycall.call(ep, "nosuch", "op", None)
        assert ei.value.code == E.E_NOT_FOUND
        with pytest.raises(PolycallError) as ei:
            pypolycall.call(ep, "debug", "sleep", '{"ms":3000}', timeout_ms=200)
        assert ei.value.code == E.E_TIMEOUT
    finally:
        r = subprocess.run([cli, "daemon", "stop", str(pf)], capture_output=True, env=env, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    with pytest.raises(PolycallError) as ei:
        pypolycall.call(ep, "debug", "echo", None, 1000)
    assert ei.value.code == E.E_TRANSPORT
