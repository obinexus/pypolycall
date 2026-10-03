"""Library discovery failures give clear errors (each case in a fresh
subprocess, because a process loads the library only once)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap

import pytest

from conftest import require_or_skip

PROBE = textwrap.dedent("""
    import sys, pypolycall
    try:
        pypolycall.version()
    except pypolycall.PolycallLibraryError as exc:
        print("LIBERR", exc); sys.exit(3)
    print("LOADED", pypolycall.library_path())
""")


def run_probe(env_lib: str, code: str = PROBE) -> subprocess.CompletedProcess:
    env = {**os.environ, "POLYCALL_LIBRARY": env_lib}
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          env=env, timeout=60)


def test_missing_library_is_a_clear_error(tmp_path):
    r = run_probe(str(tmp_path / "nope" / "libpolycall.so.1"))
    assert r.returncode == 3, r.stdout + r.stderr
    assert "libpolycall not found" in r.stdout and "POLYCALL_LIBRARY" in r.stdout


def _foreign_library() -> str | None:
    if sys.platform == "win32":
        return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "kernel32.dll")
    for cand in ("/lib/x86_64-linux-gnu/libz.so.1", "/usr/lib/x86_64-linux-gnu/libz.so.1",
                 "/usr/lib/libz.so.1", "/lib/x86_64-linux-gnu/libc.so.6"):
        if os.path.exists(cand):
            return cand
    return None


def test_foreign_library_reports_missing_symbols():
    lib = _foreign_library()
    if not lib:
        require_or_skip("POLYCALL_REQUIRE_LOADER_TESTS", "no foreign shared library found to load")
    r = run_probe(lib)
    assert r.returncode == 3, r.stdout + r.stderr
    assert "lacks polycall_" in r.stdout and ">= 1.1.0" in r.stdout


def test_real_old_library_without_abi_v1_is_refused():
    """The REAL polycall 1.0.0 library (built from the core's v1.0.0 commit
    and named by POLYCALL_TEST_OLD_LIBRARY): it loads, but lacks every ABI v1
    symbol, so pypolycall refuses it with a clear error instead of crashing."""
    old = os.environ.get("POLYCALL_TEST_OLD_LIBRARY")
    if not old or not os.path.exists(old):
        require_or_skip("POLYCALL_REQUIRE_LOADER_TESTS",
                        "POLYCALL_TEST_OLD_LIBRARY does not name a polycall 1.0 library")
    r = run_probe(old)
    assert r.returncode == 3, r.stdout + r.stderr
    assert "lacks polycall_" in r.stdout and ">= 1.1.0" in r.stdout and old in r.stdout


def test_incompatible_abi_is_refused(tmp_path):
    cc = shutil.which("cc") or shutil.which("gcc")
    if not cc:
        require_or_skip("POLYCALL_REQUIRE_LOADER_TESTS",
                        "needs a C compiler (cc/gcc) to build the fake library")
    names = ["polycall_get_version", "polycall_ffi_version", "polycall_strerror",
             "polycall_last_error", "polycall_ffi_run_config", "polycall_ffi_describe",
             "polycall_call", "polycall_peer_open", "polycall_peer_close",
             "polycall_peer_endpoint", "polycall_peer_node_id", "polycall_peer_register",
             "polycall_peer_unregister", "polycall_peer_list", "polycall_peer_ping",
             "polycall_peer_send", "polycall_peer_recv", "polycall_peer_cancel",
             "polycall_peer_health"]
    src = tmp_path / "fake.c"
    src.write_text("int polycall_ffi_abi_version(void){return 2;}\n" +
                   "".join(f"int {n}(void){{return -15;}}\n" for n in names))
    so = tmp_path / ("fake.dll" if sys.platform == "win32" else "libfake.so")
    subprocess.run([cc, "-shared", "-fPIC", "-o", str(so), str(src)], check=True)
    r = run_probe(str(so))
    assert r.returncode == 3, r.stdout + r.stderr
    assert "implements binding ABI 2" in r.stdout


def test_auto_backend_falls_back_to_python(tmp_path):
    code = textwrap.dedent("""
        import pypolycall
        with pypolycall.open_peer("fb-a") as a, pypolycall.open_peer("fb-b") as b:
            assert a.backend == "python" and b.backend == "python", (a.backend, b.backend)
            a.register("fb-b", b.endpoint)
            mid = a.send("fb-b", b"via fallback")
            assert b.recv(3000) == ("fb-a", mid, b"via fallback")
        try:
            pypolycall.open_peer("x", backend="native")
        except pypolycall.PolycallLibraryError:
            print("NATIVE-REFUSED")
        print("FALLBACK-OK")
    """)
    r = run_probe(str(tmp_path / "missing.so"), code)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "FALLBACK-OK" in r.stdout and "NATIVE-REFUSED" in r.stdout
    assert "pure-Python polycall-peer/1 fallback" in r.stderr
