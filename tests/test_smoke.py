"""pypolycall smoke test. Requires the shared core to be built first.

Run:  cd bindings/pypolycall && python -m pytest -q
(auto-skips cleanly if the shared library isn't built yet)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pypolycall  # noqa: E402


def _has_lib() -> bool:
    try:
        pypolycall.load_library()
        return True
    except OSError:
        return False


def test_version():
    if not _has_lib():
        print("shared libpolycall not built; skipping")
        return
    assert pypolycall.version() == "1.5.0"


def test_run_defaults():
    if not _has_lib():
        return
    assert pypolycall.run_config(None, run=True) == 0


if __name__ == "__main__":
    if not _has_lib():
        print("shared libpolycall not built; build the core first (./setup.sh)")
        sys.exit(0)
    print("pypolycall", pypolycall.version())
    print(pypolycall.describe(None))
    print("smoke:", "PASS" if pypolycall.run_config(None) == 0 else "FAIL")
