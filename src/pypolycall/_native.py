"""Loading libpolycall and declaring the binding ABI v1 with ctypes.

Discovery order:
  1. $POLYCALL_LIBRARY (an explicit file path)
  2. the copy bundled in this wheel (pypolycall/_native/)
  3. the system: polycall.dll / libpolycall.dll on Windows,
     libpolycall.so.1 on Linux, libpolycall.1.dylib on macOS

Every symbol is resolved and the ABI version checked when the library is
loaded, so an old (1.0) or foreign library fails here with a clear
PolycallLibraryError -- never later with a crash. ctypes releases the GIL
around every foreign call, so a blocking receive does not stall other
Python threads. The library never returns memory to free: all outputs are
written into buffers owned by this module.
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
from pathlib import Path

from .errors import E_INVALID_ARGUMENT, PolycallError, PolycallLibraryError

ABI_VERSION = 1

c_int, c_int32, c_uint32, c_size_t = ctypes.c_int, ctypes.c_int32, ctypes.c_uint32, ctypes.c_size_t
c_char_p, c_void_p = ctypes.c_char_p, ctypes.c_void_p
P_int32, P_size_t = ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(ctypes.c_size_t)

# name -> (restype, argtypes); exactly the declarations in polycall.h
SIGNATURES = {
    "polycall_get_version": (c_char_p, []),
    "polycall_ffi_abi_version": (c_int, []),
    "polycall_ffi_version": (c_int, [c_char_p, c_int]),
    "polycall_strerror": (c_char_p, [c_int]),
    "polycall_last_error": (c_int, [c_char_p, c_size_t]),
    "polycall_ffi_run_config": (c_int, [c_char_p, c_int]),
    "polycall_ffi_describe": (c_int, [c_char_p, c_char_p, c_int]),
    "polycall_call": (c_int, [c_char_p, c_char_p, c_char_p, c_char_p, c_uint32,
                              c_char_p, c_size_t, P_size_t]),
    "polycall_peer_open": (c_int, [c_char_p, c_char_p, c_char_p, P_int32]),
    "polycall_peer_close": (c_int, [c_int32]),
    "polycall_peer_endpoint": (c_int, [c_int32, c_char_p, c_size_t]),
    "polycall_peer_node_id": (c_int, [c_int32, c_char_p, c_size_t]),
    "polycall_peer_register": (c_int, [c_int32, c_char_p, c_char_p]),
    "polycall_peer_unregister": (c_int, [c_int32, c_char_p]),
    "polycall_peer_list": (c_int, [c_int32, c_char_p, c_size_t, P_size_t]),
    "polycall_peer_ping": (c_int, [c_int32, c_char_p, c_uint32]),
    "polycall_peer_send": (c_int, [c_int32, c_char_p, c_void_p, c_size_t, c_char_p, c_uint32]),
    "polycall_peer_recv": (c_int, [c_int32, c_uint32, c_char_p, c_size_t, c_char_p, c_size_t,
                                   c_void_p, c_size_t, P_size_t]),
    "polycall_peer_cancel": (c_int, [c_int32]),
    "polycall_peer_health": (c_int, [c_int32, c_char_p, c_size_t, P_size_t]),
}

_lock = threading.Lock()
_lib: ctypes.CDLL | None = None
_lib_path: str | None = None


def _bundled_dir() -> Path:
    return Path(__file__).resolve().parent / "_native"


def _candidates() -> list[str]:
    out: list[str] = []
    env = os.environ.get("POLYCALL_LIBRARY")
    if env:
        return [env]                       # explicit: use exactly this or fail
    b = _bundled_dir()
    if sys.platform == "win32":
        names = ["polycall.dll", "libpolycall.dll"]
    elif sys.platform == "darwin":
        names = ["libpolycall.1.dylib", "libpolycall.dylib"]
    else:
        names = ["libpolycall.so.1", "libpolycall.so"]
    out += [str(b / n) for n in names if (b / n).exists()]
    if sys.platform == "win32":
        # Python >= 3.8 no longer searches PATH for DLLs: resolve explicitly
        for d in os.environ.get("PATH", "").split(os.pathsep):
            for n in names:
                p = os.path.join(d, n)
                if d and os.path.isfile(p):
                    out.append(p)
    else:
        out += names                       # system search (ld cache / rpath)
    return out


def _bind(lib: ctypes.CDLL, path: str) -> None:
    missing = []
    for name, (res, args) in SIGNATURES.items():
        try:
            fn = getattr(lib, name)
        except AttributeError:
            missing.append(name)
            continue
        fn.restype = res
        fn.argtypes = args
    if missing:
        raise PolycallLibraryError(
            f"{path} is not a usable libpolycall: it lacks {', '.join(missing[:4])}"
            f"{' ...' if len(missing) > 4 else ''} -- binding ABI v1 needs polycall >= 1.1.0")
    abi = lib.polycall_ffi_abi_version()
    if abi != ABI_VERSION:
        raise PolycallLibraryError(
            f"{path} implements binding ABI {abi}, but pypolycall needs ABI {ABI_VERSION}")


def load(force: bool = False) -> ctypes.CDLL:
    """Load (once) and return the native library, or raise PolycallLibraryError."""
    global _lib, _lib_path
    with _lock:
        if _lib is not None and not force:
            return _lib
        errors = []
        for cand in _candidates():
            try:
                if sys.platform == "win32" and os.path.isabs(cand):
                    os.add_dll_directory(os.path.dirname(cand))
                lib = ctypes.CDLL(cand)
            except OSError as exc:
                errors.append(f"{cand}: {exc}")
                continue
            _bind(lib, cand)               # PolycallLibraryError propagates
            _lib, _lib_path = lib, cand
            return lib
        raise PolycallLibraryError(
            "libpolycall not found. Install a pypolycall wheel (it bundles the "
            "library), install the system package (libpolycall1), or set "
            "POLYCALL_LIBRARY to the library file. Tried: " + "; ".join(errors))


def library_path() -> str | None:
    return _lib_path


def available() -> bool:
    try:
        load()
        return True
    except PolycallLibraryError:
        return False


def last_error(lib: ctypes.CDLL) -> str:
    """polycall_last_error() of this thread (Python threads are OS threads,
    so the thread-local detail belongs to the call this thread just made)."""
    buf = ctypes.create_string_buffer(1024)
    n = lib.polycall_last_error(buf, len(buf))
    if n >= len(buf):
        buf = ctypes.create_string_buffer(n + 1)
        lib.polycall_last_error(buf, len(buf))
    return buf.value.decode("utf-8", "replace")


def strerror(lib: ctypes.CDLL, rc: int) -> str:
    return lib.polycall_strerror(rc).decode("utf-8", "replace")


def error(lib: ctypes.CDLL, rc: int, output: str | None = None,
          needed: int | None = None) -> PolycallError:
    """PolycallError for a failed call: detail first (thread-local), then
    the static strerror text."""
    detail = last_error(lib)
    return PolycallError(rc, detail, output, strerror=strerror(lib, rc), needed=needed)


def check(lib: ctypes.CDLL, rc: int, output: str | None = None) -> None:
    if rc != 0:
        raise error(lib, rc, output)


def enc(s: str | None, what: str = "argument") -> bytes | None:
    """UTF-8 for the C side. A NUL inside the text would silently truncate
    it at the boundary, so it is rejected instead."""
    if s is None:
        return None
    if not isinstance(s, str):
        raise PolycallError(E_INVALID_ARGUMENT, f"{what} must be str, not {type(s).__name__}")
    if "\x00" in s:
        raise PolycallError(E_INVALID_ARGUMENT, f"{what} contains a NUL character")
    try:
        return s.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise PolycallError(E_INVALID_ARGUMENT, f"{what} is not valid Unicode: {exc}") from None
