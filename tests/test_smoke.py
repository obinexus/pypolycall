"""Smoke test of the installed package against the real library (no
silent skip: an unusable library is reported as a pytest skip by the
`native` fixture, or a failure under POLYCALL_REQUIRE_NATIVE=1)."""
import pypolycall


def test_import_and_version(native):
    assert pypolycall.__version__ == "1.1.0"
    assert pypolycall.version().startswith("1.")
    assert pypolycall.load_library() is pypolycall.load_library()


def test_run_config_compat(native, tmp_path):
    rc = tmp_path / "pypolycallrc"
    rc.write_text("log_level=info\nmax_connections=1000\n")
    assert pypolycall.run_config(str(rc)) == 0
    assert pypolycall.run_config(str(rc), run=False) == 0
