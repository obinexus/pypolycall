# @obinexusltd/pypolycall

Python `ctypes` binding for
[libpolycall](https://github.com/obinexus/libpolycall) 1.5, distributed through
both Python packaging metadata and an npm-indexable source package.

The Python adapter remains thin: calls cross the shared C FFI boundary and no
language-specific configuration parser or runtime is implemented here.

## npm source package

```shell
npm install @obinexusltd/pypolycall
```

The npm package includes and indexes:

- `src/` — Python package source
- `dist/` — wheels and source distributions when generated
- `examples/` — sample configuration and examples
- `tests/` — Python and npm integration tests

```javascript
const pypolycall = require('@obinexusltd/pypolycall');

console.log(pypolycall.directories.src.root);
console.log(pypolycall.directories.dist.relativeFiles);
console.log(pypolycall.resolve('examples', 'pypolycallrc'));
```

Each directory index exposes its absolute `root`, absolute `files`, and
portable `relativeFiles`. Exported npm subpaths also allow files to be resolved
directly, for example:

```javascript
require.resolve('@obinexusltd/pypolycall/examples/pypolycallrc');
```

## Python installation

From this source tree:

```shell
python -m pip install .
```

Build a wheel and source distribution after installing the Python `build`
frontend:

```shell
python -m pip install build
npm run build:python
```

Artifacts are written to `dist/` and become part of both the JavaScript index
and the npm package.

## Python API

```python
import pypolycall

print(pypolycall.version())
pypolycall.run_config("pypolycallrc")
print(pypolycall.describe("pypolycallrc"))
```

Build libpolycall first and place its shared library in the core `build/` or
`bin/` directory, or somewhere discoverable by the platform loader.

## Verification

```shell
npm test
```

This validates npm metadata, all four directory indexes, subpath exports, path
containment, and the existing Python smoke test. The Python test exits cleanly
when the shared libpolycall core has not been built.

## Author and license

Copyright © 2026 Nnamdi Michael Okpala
<okpalan@protonmail.com>.

Released under the [MIT License](LICENSE).
