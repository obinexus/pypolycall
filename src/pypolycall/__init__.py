"""pypolycall -- Python binding for Polycall.

* Native path: ctypes over libpolycall's binding ABI v1 (polycall.h). The
  wheel bundles the library; POLYCALL_LIBRARY overrides it.
* Fallback path: a pure-Python polycall-peer/1 node (pypolycall.fallback),
  wire-compatible with the C node, used when the library is unavailable.

>>> import pypolycall
>>> pypolycall.version()                      # library version, e.g. '1.1.0'
>>> pypolycall.run_config("pypolycallrc")     # raises PolycallError if invalid
>>> with pypolycall.open_peer("beta") as p:   # native, or Python fallback
...     p.register("alpha", "127.0.0.1:9001")
...     p.send("alpha", b"hello")
"""
from __future__ import annotations

import ctypes
import logging

from . import _native
from .errors import (NAMES, OK, PolycallError, PolycallLibraryError, check_u32)
from .fallback import FallbackPeer
from .peer import Peer

__all__ = [
    "__version__", "version", "abi_version", "library_path", "native_available",
    "strerror", "run_config", "describe", "call", "Peer", "FallbackPeer", "open_peer",
    "load_library", "PolycallError", "PolycallLibraryError", "NAMES",
]
__version__ = "1.1.0"
log = logging.getLogger("pypolycall")


def native_available() -> bool:
    """True when a compatible libpolycall (binding ABI v1) can be loaded."""
    return _native.available()


def library_path() -> str | None:
    """The libpolycall file in use (after the first native call), or None."""
    return _native.library_path()


def abi_version() -> int:
    return _native.load().polycall_ffi_abi_version()


def version() -> str:
    """Version of the loaded libpolycall (e.g. '1.1.0')."""
    lib = _native.load()
    buf = ctypes.create_string_buffer(32)
    lib.polycall_ffi_version(buf, len(buf))
    return buf.value.decode("ascii")


def load_library() -> ctypes.CDLL:
    """The loaded libpolycall (kept from pypolycall 1.0 for compatibility)."""
    return _native.load()


def strerror(code: int) -> str:
    """polycall_strerror(code): the library's static name for any status."""
    return _native.strerror(_native.load(), code)


def run_config(path: str, strict: bool = True, *, run: bool | None = None) -> int:
    """Validate a Polycallfile / Polycallrc / *-polycallrc through the core's
    shared configuration interface, polycall_ffi_run_config() -- the grammar
    of `polycall config validate`; nothing is parsed in Python. Returns 0
    (raises PolycallError otherwise).

    strict=True (run=1, the default): validate for running with this build --
    unknown keys are errors and settings it cannot honour (tls_enabled=true)
    are E_UNSUPPORTED. strict=False (run=0): unknown keys are warnings, as
    `polycall config validate` reports them. ``run=`` is the 1.0 spelling."""
    if run is not None:
        strict = bool(run)
    lib = _native.load()
    _native.check(lib, lib.polycall_ffi_run_config(_native.enc(path, "config path"),
                                                   1 if strict else 0))
    return 0


def describe(path: str) -> str:
    """polycall_ffi_describe(): JSON description of a configuration file
    (layer, servers, peers, key/values; secrets never resolved), as JSON
    text (1.0 compatible; json.loads() it for a dict)."""
    lib = _native.load()
    p = _native.enc(path, "config path")
    cap = 4096
    while True:
        buf = ctypes.create_string_buffer(cap)
        n = lib.polycall_ffi_describe(p, buf, cap)
        if n < 0:
            _native.check(lib, n)
        if n < cap:
            return buf.value.decode("utf-8")
        cap = n + 1                    # snprintf rules: n is the full length


def call(endpoint: str, service: str, operation: str, input_json: str | None = None,
         timeout_ms: int = 5000) -> str:
    """One polycall_rpc v1 round trip to a running runtime / daemon
    (`polycall start` / `polycall daemon start`). Returns the operation's
    output JSON text; raises PolycallError (with .output = the remote error
    object JSON) on failure. Executes once, never retried here.
    timeout_ms: 1..600000 (the library rejects other values)."""
    t = check_u32(timeout_ms, "timeout_ms")
    lib = _native.load()
    # sized for POLYCALL_CALL_MAX_OUTPUT up front: a too-small buffer would
    # discard a result whose operation already ran (never re-executed here)
    cap = (1 << 20) + 1
    out = ctypes.create_string_buffer(cap)
    n = ctypes.c_size_t(0)
    rc = lib.polycall_call(_native.enc(endpoint, "endpoint"), _native.enc(service, "service"),
                           _native.enc(operation, "operation"),
                           _native.enc(input_json, "input_json"), t, out, cap, ctypes.byref(n))
    text = out.value.decode("utf-8", "replace")
    if rc != OK:
        raise _native.error(lib, rc, text, needed=n.value if n.value > cap else None)
    return text


def open_peer(node_id: str, bind: str | None = "127.0.0.1:0", token: str | None = None,
              backend: str = "auto"):
    """Open a peer node. backend: 'native' (libpolycall, error if missing),
    'python' (pure-Python fallback), or 'auto' (native when a compatible
    library loads, otherwise the fallback -- logged at WARNING)."""
    if backend == "native":
        return Peer(node_id, bind, token)
    if backend == "python":
        return FallbackPeer(node_id, bind, token)
    if backend != "auto":
        raise ValueError("backend must be 'auto', 'native' or 'python'")
    try:
        _native.load()
    except PolycallLibraryError as exc:
        log.warning("libpolycall unavailable (%s); using the pure-Python polycall-peer/1 fallback", exc)
        return FallbackPeer(node_id, bind, token)
    return Peer(node_id, bind, token)
