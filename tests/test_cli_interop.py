"""Cross-implementation interop with the C `polycall` CLI: separate OS
processes exchanging payloads both ways, and RPC calls into a runtime."""
from __future__ import annotations

import base64
import json
import os
import subprocess

import pytest

import pypolycall
from pypolycall import FallbackPeer, PolycallError
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
    finally:
        node.close()


def test_call_into_runtime(native, cli, cli_proc, tmp_path):
    epf = tmp_path / "rt.ep"
    cli_proc(cli, "start", "--endpoint", "127.0.0.1:0", "--endpoint-file", str(epf),
             "--auth-token-env", "PYPOLYCALL_TEST_TOKEN", env={"PYPOLYCALL_TEST_TOKEN": "rt"})
    ep = wait_file(str(epf))
    out = json.loads(pypolycall.call(ep, "inventory", "get", '{"item_id":"widget-a"}'))
    assert out == {"item_id": "widget-a", "quantity": 42, "in_stock": True}
    nested = '{"a":[1,{"b":"x"}],"s":"' + "v" * 600 + '"}'
    assert json.loads(pypolycall.call(ep, "debug", "echo", nested)) == {"echo": json.loads(nested)}
    with pytest.raises(PolycallError) as ei:
        pypolycall.call(ep, "inventory", "teleport", "{}")
    assert ei.value.code == E.E_NOT_FOUND and "operation.unknown" in ei.value.output
    with pytest.raises(PolycallError) as ei:
        pypolycall.call(ep, "debug", "sleep", '{"ms":2000}', timeout_ms=100)
    assert ei.value.code == E.E_TIMEOUT
    with pytest.raises(PolycallError) as ei:
        pypolycall.call(ep, "debug", "echo", "{not json")
    assert ei.value.code == E.E_INVALID_ARGUMENT
    subprocess.run([cli, "stop", "--endpoint", ep, "--auth-token-env", "PYPOLYCALL_TEST_TOKEN"],
                   env={**os.environ, "PYPOLYCALL_TEST_TOKEN": "rt"}, timeout=30)
    with pytest.raises(PolycallError) as ei:
        pypolycall.call("127.0.0.1:1", "a", "b", None, 1000)
    assert ei.value.code == E.E_TRANSPORT
