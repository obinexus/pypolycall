"""python -m pypolycall <command> ... (also installed as `pypolycall`).

  version                         library + binding versions, backend in use
  call ENDPOINT SERVICE OP [JSON] [--timeout-ms N]
                                  one polycall_rpc v1 call (polycall_call)
  peer-serve --node-id ID [--bind H:P] [--peer ID=H:P ...] [--backend B]
             [--endpoint-file F]  run a node; print received messages as JSON lines
The shared token is read from $POLYCALL_DEV_TOKEN.

Configuration files are not handled by this tool: validate them with the
core's own CLI (`polycall config validate FILE`) or, from Python, with
pypolycall.run_config() / pypolycall.describe(), which call the core's
polycall_ffi_run_config() / polycall_ffi_describe().
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import signal
import sys
import threading

from . import (PolycallError, PolycallLibraryError, __version__, call, library_path,
               native_available, open_peer, version)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pypolycall", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("version")
    p = sub.add_parser("call")
    p.add_argument("endpoint")
    p.add_argument("service")
    p.add_argument("operation")
    p.add_argument("input", nargs="?")
    p.add_argument("--timeout-ms", type=int, default=5000)
    p = sub.add_parser("peer-serve")
    p.add_argument("--node-id", required=True)
    p.add_argument("--bind", default="127.0.0.1:0")
    p.add_argument("--peer", action="append", default=[])
    p.add_argument("--backend", default="auto", choices=["auto", "native", "python"])
    p.add_argument("--endpoint-file")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "version":
            print(json.dumps({"pypolycall": __version__,
                              "native": native_available(),
                              "library": version() if native_available() else None,
                              "library_path": library_path()}))
        elif a.cmd == "call":
            print(call(a.endpoint, a.service, a.operation, a.input, a.timeout_ms))
        elif a.cmd == "peer-serve":
            node = open_peer(a.node_id, a.bind, os.environ.get("POLYCALL_DEV_TOKEN"), a.backend)
            for spec in a.peer:
                pid, _, ep = spec.partition("=")
                node.register(pid, ep)
            if a.endpoint_file:
                with open(a.endpoint_file, "w", encoding="utf-8") as f:
                    f.write(node.endpoint + "\n")
            print(json.dumps({"event": "listening", "node_id": a.node_id,
                              "endpoint": node.endpoint, "backend": node.backend}), flush=True)
            stop = threading.Event()
            signal.signal(signal.SIGINT, lambda *_: stop.set())
            if hasattr(signal, "SIGTERM"):
                signal.signal(signal.SIGTERM, lambda *_: stop.set())
            while not stop.is_set():
                try:
                    frm, mid, data = node.recv(200)
                except PolycallError as exc:
                    if exc.name == "POLYCALL_E_TIMEOUT":
                        continue
                    raise
                print(json.dumps({"event": "message", "from": frm, "id": mid,
                                  "payload_b64": base64.b64encode(data).decode("ascii")}), flush=True)
            node.close()
            print(json.dumps({"event": "stopped", "node_id": a.node_id}), flush=True)
    except PolycallLibraryError as exc:
        print(f"pypolycall: {exc}", file=sys.stderr)
        return 4
    except PolycallError as exc:
        print(f"pypolycall: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
