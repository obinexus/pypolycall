"""pypolycall - Python reference adapter for libpolycall.

A thin ctypes adapter over the shared core. There is NO config parsing or
runtime logic here: every call forwards across the FFI boundary declared in
``include/polycall/polycall_ffi.h``. This is the template for other scripting
bindings (Ruby, PHP, Tcl, ...).

Usage
-----
>>> import pypolycall
>>> pypolycall.version()
'1.5.0'
>>> pypolycall.run_config("pypolycallrc")      # -> 0 on success
>>> print(pypolycall.describe("pypolycallrc"))
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes.util import find_library

__all__ = ["version", "run_config", "describe", "PolycallError", "load_library"]
__version__ = "1.5.0"


class PolycallError(RuntimeError):
    """Raised when the core returns a non-zero polycall_status_t."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(f"{message} (status={code})")
        self.code = code


def _candidate_paths() -> list[str]:
    """Locate the shared libpolycall built by the core (setup.sh / CMake)."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, "..", "..", "..", ".."))
    names = ["libpolycall.dll", "polycall.dll", "libpolycall.so", "libpolycall.dylib"]
    dirs = [os.path.join(root, "build"), os.path.join(root, "bin"), root]
    found = [os.path.join(d, n) for d in dirs for n in names]
    lib = find_library("polycall")
    if lib:
        found.append(lib)
    return found


def load_library() -> ctypes.CDLL:
    """Load the shared core, trying the usual build output locations."""
    last = None
    for path in _candidate_paths():
        if os.path.exists(path) or not os.path.dirname(path):
            try:
                return ctypes.CDLL(path)
            except OSError as exc:  # keep trying other candidates
                last = exc
    raise OSError(
        "could not load the shared libpolycall. Build the core first "
        "(run ./setup.sh or cmake --build build). Last error: %s" % last
    )


# Lazily bound on first use so `import pypolycall` never fails at import time.
_lib: ctypes.CDLL | None = None


def _core() -> ctypes.CDLL:
    global _lib
    if _lib is None:
        _lib = load_library()
        _lib.polycall_ffi_version.argtypes = [ctypes.c_char_p, ctypes.c_int]
        _lib.polycall_ffi_version.restype = ctypes.c_int
        _lib.polycall_ffi_run_config.argtypes = [ctypes.c_char_p, ctypes.c_int]
        _lib.polycall_ffi_run_config.restype = ctypes.c_int
        _lib.polycall_ffi_describe.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
        _lib.polycall_ffi_describe.restype = ctypes.c_int
    return _lib


def version() -> str:
    buf = ctypes.create_string_buffer(64)
    _core().polycall_ffi_version(buf, len(buf))
    return buf.value.decode("utf-8")


def run_config(path: str | None = None, run: bool = True) -> int:
    p = path.encode("utf-8") if path else None
    rc = _core().polycall_ffi_run_config(p, 1 if run else 0)
    if rc != 0:
        raise PolycallError(f"run_config({path!r}) failed", rc)
    return rc


def describe(path: str | None = None) -> str:
    buf = ctypes.create_string_buffer(256)
    p = path.encode("utf-8") if path else None
    rc = _core().polycall_ffi_describe(p, buf, len(buf))
    text = buf.value.decode("utf-8", "replace")
    if rc != 0:
        raise PolycallError(f"describe({path!r}) failed: {text}", rc)
    return text


if __name__ == "__main__":  # tiny CLI: python -m pypolycall [config]
    cfg = sys.argv[1] if len(sys.argv) > 1 else None
    print(f"pypolycall using libpolycall {version()}")
    print(describe(cfg))
    sys.exit(run_config(cfg))
