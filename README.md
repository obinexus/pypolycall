# pypolycall

Python binding for [Polycall](https://github.com/obinexus/polycall):
`ctypes` over the libpolycall **binding ABI v1** (`polycall.h`), plus a
pure-Python implementation of the `polycall-peer/1` protocol that takes over
when the native library is unavailable (the NSIGII "HTTP fallback").

| | |
| --- | --- |
| PyPI name | `pypolycall` (1.0.0 is the published version; this branch builds 1.1.0, **not yet published**) |
| Python | 3.9 – 3.14 |
| Wheels | `py3-none-<platform>`: the wheel bundles `libpolycall` built from the vendored core (`core/VENDORED.txt`); one wheel per platform serves every Python 3 |
| sdist | builds `libpolycall` from source (needs a C compiler; CMake/Ninja are fetched by the isolated build) |
| Native library | bundled copy; override with `POLYCALL_LIBRARY=/path/to/libpolycall.so.1` (or `polycall.dll`) |

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

Errors: `PolycallError` (`.code`, `.name`, `.detail`, `.output`) for
operation failures; `PolycallLibraryError` when the library is missing,
lacks the ABI v1 symbols (polycall < 1.1.0) or reports another ABI version.

Delivery: `send` makes exactly one attempt. `E_TRANSPORT` = not delivered;
`E_TIMEOUT` = unknown — retry with the **same** message id and the receiver
keeps exactly one copy.

## Build and test

```bash
python -m pip install build pytest
python -m build                              # sdist + wheel in dist/
python -m pip install dist/pypolycall-1.1.0-*.whl
cd /tmp && POLYCALL_REQUIRE_NATIVE=1 POLYCALL_CLI=$(command -v polycall) \
    python -m pytest /path/to/pypolycall/tests
```

Re-vendor the core after a core change:
`python scripts/vendor_core.py ../../polycall`.

## License

MIT — see [LICENSE](LICENSE).
