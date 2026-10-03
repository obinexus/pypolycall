"""Smoke test of the installed package against the real library (no
silent skip: an unusable library is reported as a pytest skip by the
`native` fixture, or a failure under POLYCALL_REQUIRE_NATIVE=1)."""
import os

import pytest

import pypolycall


def test_import_and_version(native):
    assert pypolycall.__version__ == "1.1.0"
    assert pypolycall.version().startswith("1.")
    assert pypolycall.load_library() is pypolycall.load_library()


def test_installed_distribution_metadata():
    """When run against an installed wheel/sdist (not the source tree), the
    distribution metadata and the module agree on the version."""
    from importlib import metadata
    try:
        dist = metadata.distribution("pypolycall")
    except metadata.PackageNotFoundError:
        if os.environ.get("PYPOLYCALL_EXPECT_INSTALLED") == "1":
            pytest.fail("pypolycall is not installed as a distribution")
        pytest.skip("running from the source tree (pypolycall not installed)")
    assert dist.version == pypolycall.__version__
    if os.environ.get("PYPOLYCALL_EXPECT_INSTALLED") == "1":
        assert "site-packages" in pypolycall.__file__.replace("\\", "/")


def test_run_config_compat(native, tmp_path):
    rc = tmp_path / "pypolycallrc"
    rc.write_text("log_level=info\nmax_connections=1000\n")
    assert pypolycall.run_config(str(rc)) == 0
    assert pypolycall.run_config(str(rc), run=False) == 0


def test_library_source(native):
    """Which libpolycall is in use: the wheel's bundled copy (no
    POLYCALL_LIBRARY) or exactly the file POLYCALL_LIBRARY names."""
    path = pypolycall.library_path()
    env = os.environ.get("POLYCALL_LIBRARY")
    if env:
        assert path == env
    elif os.environ.get("PYPOLYCALL_EXPECT_BUNDLED") == "1":
        norm = path.replace("\\", "/")
        assert "/pypolycall/_native/" in norm, path
    else:
        pytest.skip(f"library found by system search: {path}")
