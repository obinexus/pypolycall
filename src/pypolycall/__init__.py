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
from .errors import (E_TOO_LARGE, NAMES, OK, PolycallError, PolycallLibraryError)
from .fallback import FallbackPeer
from .peer import Peer

__all__ = [
    "__version__", "version", "abi_version", "library_path", "native_available",
    "run_config", "describe", "call", "Peer", "FallbackPeer", "open_peer", "load_library",
    "PolycallError", "PolycallLibraryError", "NAMES",
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


def run_config(path: str, strict: bool = True, *, run: bool | None = None) -> int:
    """Validate a Polycallfile / Polycallrc / *-polycallrc with the core's
    grammar; returns 0 (raises PolycallError otherwise). strict=True (the
    historical run=1): unknown keys and settings this build cannot honour
    (tls_enabled=true) are errors. ``run=`` is the 1.0 spelling of strict."""
    if run is not None:
        strict = bool(run)
    lib = _native.load()
    _native.check(lib, lib.polycall_ffi_run_config(_native.enc(path), 1 if strict else 0))
    return 0


def describe(path: str) -> str:
    """JSON description of a configuration file, as JSON text (1.0
    compatible; json.loads() it for a dict)."""
    lib = _native.load()
    p = _native.enc(path)
    n = lib.polycall_ffi_describe(p, None, 0)
    if n < 0:
        _native.check(lib, n)
    buf = ctypes.create_string_buffer(n + 1)
    n2 = lib.polycall_ffi_describe(p, buf, n + 1)
    if n2 < 0:
        _native.check(lib, n2)
    return buf.value.decode("utf-8")


def call(endpoint: str, service: str, operation: str, input_json: str | None = None,
         timeout_ms: int = 5000) -> str:
    """One polycall_rpc v1 round trip to a running runtime / daemon. Returns
    the operation's output JSON text; raises PolycallError (with .output =
    the remote error object JSON) on failure. Never retried here."""
    lib = _native.load()
    # sized for POLYCALL_CALL_MAX_OUTPUT up front: a too-small buffer would
    # discard a result whose operation already ran (never re-executed here)
    cap = (1 << 20) + 1
    out = ctypes.create_string_buffer(cap)
    n = ctypes.c_size_t(0)
    rc = lib.polycall_call(_native.enc(endpoint), _native.enc(service), _native.enc(operation),
                           _native.enc(input_json), timeout_ms, out, cap, ctypes.byref(n))
    text = out.value.decode("utf-8")
    if rc != OK:
        raise PolycallError(rc, _native.last_error(lib), text)
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
