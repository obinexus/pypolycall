"""Status codes and exceptions (mirrors the POLYCALL_* codes in polycall.h)."""
from __future__ import annotations

OK = 0
E_INVALID_ARGUMENT = -1
E_NO_MEMORY = -2
E_INVALID_HANDLE = -3
E_TIMEOUT = -4
E_TRANSPORT = -5
E_PROTOCOL = -6
E_NOT_FOUND = -7
E_AUTH = -8
E_REMOTE = -9
E_TOO_LARGE = -10
E_BUSY = -11
E_CANCELLED = -12
E_CONFIG = -13
E_ADDRESS_IN_USE = -14
E_UNSUPPORTED = -15
E_PERMISSION = -16
E_CLOSED = -17
E_INTERNAL = -18

NAMES = {
    OK: "POLYCALL_OK",
    E_INVALID_ARGUMENT: "POLYCALL_E_INVALID_ARGUMENT",
    E_NO_MEMORY: "POLYCALL_E_NO_MEMORY",
    E_INVALID_HANDLE: "POLYCALL_E_INVALID_HANDLE",
    E_TIMEOUT: "POLYCALL_E_TIMEOUT",
    E_TRANSPORT: "POLYCALL_E_TRANSPORT",
    E_PROTOCOL: "POLYCALL_E_PROTOCOL",
    E_NOT_FOUND: "POLYCALL_E_NOT_FOUND",
    E_AUTH: "POLYCALL_E_AUTH",
    E_REMOTE: "POLYCALL_E_REMOTE",
    E_TOO_LARGE: "POLYCALL_E_TOO_LARGE",
    E_BUSY: "POLYCALL_E_BUSY",
    E_CANCELLED: "POLYCALL_E_CANCELLED",
    E_CONFIG: "POLYCALL_E_CONFIG",
    E_ADDRESS_IN_USE: "POLYCALL_E_ADDRESS_IN_USE",
    E_UNSUPPORTED: "POLYCALL_E_UNSUPPORTED",
    E_PERMISSION: "POLYCALL_E_PERMISSION",
    E_CLOSED: "POLYCALL_E_CLOSED",
    E_INTERNAL: "POLYCALL_E_INTERNAL",
}


UINT32_MAX = 0xFFFFFFFF


class PolycallError(Exception):
    """A Polycall operation failed.

    ``code``     the POLYCALL_E_* status (negative int)
    ``strerror`` the library's ``polycall_strerror(code)`` text, e.g.
                 ``"POLYCALL_E_TIMEOUT: deadline exceeded"`` (for errors raised
                 by the pure-Python fallback or by argument checks before the
                 library is reached: the symbolic name alone)
    ``name``     the symbolic name, e.g. ``"POLYCALL_E_TIMEOUT"``
    ``detail``   the library's ``polycall_last_error()`` text for this failure
    ``output``   for call(): the remote error object JSON, if any
    ``needed``   for E_TOO_LARGE on a caller buffer: the size required
    """

    def __init__(self, code: int, detail: str = "", output: str | None = None, *,
                 strerror: str | None = None, needed: int | None = None) -> None:
        self.code = code
        self.strerror = strerror or NAMES.get(code, "POLYCALL_E_UNKNOWN")
        self.name = self.strerror.split(":", 1)[0]
        self.detail = detail
        self.output = output or None  # e.g. the remote error object of call()
        self.needed = needed
        super().__init__(f"{self.name} ({code}): {detail}" if detail else f"{self.name} ({code})")


def check_u32(value: object, what: str) -> int:
    """Validate an integer that crosses the ABI as uint32_t (timeouts).
    ctypes would silently truncate out-of-range values (-1 -> 4294967295,
    2**32 + 5 -> 5), so they are rejected here instead."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise PolycallError(E_INVALID_ARGUMENT, f"{what} must be an int, not {type(value).__name__}")
    if not 0 <= value <= UINT32_MAX:
        raise PolycallError(E_INVALID_ARGUMENT, f"{what} must be 0..{UINT32_MAX}, got {value}")
    return value


class PolycallLibraryError(OSError):
    """The native libpolycall could not be used: not found, missing symbols
    (older than binding ABI v1) or an incompatible ABI version."""
