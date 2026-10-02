"""Shared fixtures. Nothing here ever turns a missing prerequisite into a
pass: missing things are pytest skips with a reason, and with
POLYCALL_REQUIRE_NATIVE=1 (set by the release QA) an unusable native
library is a failure."""
from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import time

import pytest

import pypolycall


@pytest.fixture(scope="session")
def native():
    if not pypolycall.native_available():
        try:
            from pypolycall import _native
            _native.load()
        except Exception as exc:  # noqa: BLE001 - report the exact reason
            reason = str(exc)
        else:
            reason = "unknown"
        if os.environ.get("POLYCALL_REQUIRE_NATIVE") == "1":
            pytest.fail(f"native libpolycall required but unusable: {reason}")
        pytest.skip(f"native libpolycall unavailable: {reason}")
    return True


@pytest.fixture(scope="session")
def cli():
    path = os.environ.get("POLYCALL_CLI") or shutil.which("polycall")
    if not path:
        if os.environ.get("POLYCALL_REQUIRE_CLI") == "1":
            pytest.fail("polycall CLI required (POLYCALL_CLI) but not found")
        pytest.skip("polycall CLI not found: set POLYCALL_CLI to run interop tests")
    return path


@pytest.fixture
def token(monkeypatch):
    t = "pt-" + secrets.token_hex(8)
    monkeypatch.setenv("POLYCALL_DEV_TOKEN", t)
    return t


def wait_file(path: str, timeout: float = 10.0) -> str:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with open(path, encoding="utf-8") as f:
                txt = f.read().strip()
            if txt:
                return txt
        except FileNotFoundError:
            pass
        time.sleep(0.05)
    raise TimeoutError(f"{path} not written within {timeout}s")


@pytest.fixture
def cli_proc(tmp_path):
    procs: list[subprocess.Popen] = []

    def start(*args: str, env: dict | None = None) -> subprocess.Popen:
        p = subprocess.Popen(list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env={**os.environ, **(env or {})})
        procs.append(p)
        return p

    yield start
    for p in procs:
        if p.poll() is None:
            p.kill()
        p.wait(timeout=10)
