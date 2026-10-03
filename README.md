# pypolycall

Python binding for [Polycall](https://github.com/obinexus/polycall):
`ctypes` over the libpolycall **binding ABI v1** (`polycall.h`), plus a
pure-Python implementation of the `polycall-peer/1` protocol that takes over
when the native library is unavailable (the NSIGII "HTTP fallback").

| | |
| --- | --- |
| PyPI name | `pypolycall` (1.0.0 is the published version; this branch builds 1.1.0, **not yet published**) |
| Python | 3.9 – 3.14 (CPython) |
| Wheels | `py3-none-<platform>`: the wheel bundles `libpolycall` built from the vendored core (`core/VENDORED.txt`); one wheel per platform serves every Python 3. Linux: `manylinux_2_17_x86_64` (built in `manylinux_2_28`, checked with `auditwheel`) |
| sdist | builds `libpolycall` from source (needs a C compiler; CMake/Ninja are fetched by the isolated build when the system has none) |
| Native library | bundled copy; override with `POLYCALL_LIBRARY=/path/to/libpolycall.so.1` (or `polycall.dll` / `libpolycall.dll`) |
| Platforms | Linux x86_64; Windows x64 with either core build (`polycall.dll`, MSVC, or `libpolycall.dll`, MinGW/UCRT64) through `POLYCALL_LIBRARY` or `PATH` |

## Install

```bash
python -m pip install pypolycall            # once 1.1.0 is published
python -m pip install ./dist/pypolycall-1.1.0-py3-none-*.whl   # a locally built wheel
```

## Use

```python
import pypolycall

pypolycall.version()                         # "1.1.0" (the loaded libpolycall)
pypolycall.run_config("pypolycallrc")        # raises PolycallError if invalid
out = pypolycall.call("127.0.0.1:8084", "inventory", "get", '{"item_id":"widget-a"}')

with pypolycall.open_peer("beta", "127.0.0.1:9002", token="...") as node:
    node.register("alpha", "127.0.0.1:9001")
    mid = node.send("alpha", b"hello")       # one acknowledged attempt
    sender, message_id, payload = node.recv(5000)
```

`open_peer(..., backend=)`: `"native"` (libpolycall, error if missing),
`"python"` (pure-Python fallback) or `"auto"` (native when a compatible
library loads, otherwise the fallback, logged at WARNING). Both backends
speak the same wire protocol and interoperate with each other and with
`polycall peer serve`.

`recv(timeout_ms=5000, capacity=None)`: `0` polls, `None` waits until a
message, `cancel()` (`E_CANCELLED`) or `close()` (`E_CLOSED`). With a fixed
`capacity`, a larger message raises `E_TOO_LARGE` with `.needed` set and
stays queued. Timeouts cross the ABI as `uint32_t`; values outside
`0..4294967295` (and strings containing NUL) are refused with
`E_INVALID_ARGUMENT` instead of being truncated by ctypes.

Errors: `PolycallError` with `.code` (the `POLYCALL_E_*` status), `.strerror`
(`polycall_strerror(code)`), `.name` (its symbolic part), `.detail`
(`polycall_last_error()`), `.output` (`call()`'s remote error object) and
`.needed`. `PolycallLibraryError` when the library is missing, lacks the ABI
v1 symbols (polycall < 1.1.0) or reports another ABI version.

Delivery: `send` makes exactly one attempt. `E_TRANSPORT` = not delivered;
`E_TIMEOUT` = unknown — retry with the **same** message id and the receiver
keeps exactly one copy.

Threads: ctypes releases the GIL for every library call, so a `recv()`
blocked in one thread does not stop other threads; all calls are
thread-safe, also on one shared node.

## Configuration files

pypolycall does not read configuration files itself. `run_config(path,
strict=True)` and `describe(path)` hand the path to the core's shared
configuration interface, `polycall_ffi_run_config()` /
`polycall_ffi_describe()` — the grammar of `polycall config validate`. On the
command line use the core's own CLI: `polycall config validate pypolycallrc`.

## Command line

`python -m pypolycall` (also installed as `pypolycall`):

| Command | Does |
| --- | --- |
| `version` | binding and library versions, library path, backend availability (JSON) |
| `call ENDPOINT SERVICE OPERATION [JSON] [--timeout-ms N]` | one `polycall_call` round trip; prints the output JSON |
| `peer-serve --node-id ID [--bind H:P] [--peer ID=H:P ...] [--backend auto\|native\|python] [--endpoint-file F]` | run a node; prints one JSON line per received message |

The shared token is read from `POLYCALL_DEV_TOKEN`. Exit codes: 0 ok,
1 `PolycallError`, 4 library unusable.

## Build and test

```bash
python -m pip install build pytest
python -m build                              # sdist, then the wheel built from it, in dist/
auditwheel repair -w wheelhouse dist/*.whl   # Linux: manylinux tag for PyPI
python -m pip install wheelhouse/pypolycall-1.1.0-*.whl
cd /tmp && POLYCALL_REQUIRE_NATIVE=1 POLYCALL_REQUIRE_CLI=1 POLYCALL_CLI=$(command -v polycall) \
    python -m pytest /path/to/pypolycall/tests
```

The tests run against the real library (the bundled copy, or
`POLYCALL_LIBRARY`) and the `polycall` CLI (`peer serve/send/recv`, `start`,
`stop`, `daemon start/stop`). A missing prerequisite is a pytest **skip** with
its reason — or a failure with `POLYCALL_REQUIRE_NATIVE=1`,
`POLYCALL_REQUIRE_CLI=1`, `POLYCALL_REQUIRE_LOADER_TESTS=1`.
`POLYCALL_TEST_OLD_LIBRARY` names a polycall 1.0 library for the
"old library without ABI v1" check.

Re-vendor the core after a core change:
`python scripts/vendor_core.py ../../polycall`.

## License

MIT — see [LICENSE](LICENSE).
