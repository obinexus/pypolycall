# TODO — pypolycall

Status: ctypes binding over the libpolycall binding ABI v1 (polycall >= 1.1.0),
with a pure-Python polycall-peer/1 fallback; tested against the real core.

- [x] `ctypes` declarations of every ABI v1 function; library loaded and
      ABI-checked up front (missing / old / foreign library -> clear error)
- [x] `run_config` / `describe` through the core's shared configuration
      interface (`polycall_ffi_run_config` / `polycall_ffi_describe`)
- [x] `call()` (polycall_rpc v1) and `Peer` (open / register / list / ping /
      send / recv / cancel / health / close)
- [x] Pure-Python polycall-peer/1 fallback, interoperating with the C node
- [x] Wheel (bundles libpolycall, `py3-none-<platform>`) and sdist (builds it)
- [x] Tests against the real library and the C CLI (Linux; Windows with
      MSYS2 CPython against `polycall.dll` and `libpolycall.dll`)
- [ ] Build and test `win_amd64` wheels with python.org CPython
- [ ] Publish 1.1.0 to PyPI (1.0.0 is the published version)
