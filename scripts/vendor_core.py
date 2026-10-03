#!/usr/bin/env python3
"""Vendor the Polycall core sources needed to build libpolycall into core/.

The sdist must build the native library from source on any platform, so the
exact core revision is copied in and recorded in core/VENDORED.txt.

    python scripts/vendor_core.py ../../polycall
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

KEEP = [
    "CMakeLists.txt",
    "LICENSE",
    "cmake",
    "include",
    "src/config",
    "src/core",
    "src/peer",
    "src/runtime",
]
KEEP_GLOB = ["src/*.c"]          # top-level core sources (main.c is skipped below)
SKIP_NAMES = {"main.c", "__pycache__"}


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    src = Path(sys.argv[1]).resolve()
    dst = Path(__file__).resolve().parent.parent / "core"
    if not (src / "include" / "polycall.h").is_file():
        print(f"{src} is not a polycall core checkout", file=sys.stderr)
        return 2
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir()
    for rel in KEEP:
        s = src / rel
        if s.is_dir():
            shutil.copytree(s, dst / rel, ignore=shutil.ignore_patterns(*SKIP_NAMES))
        else:
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, dst / rel)
    for pattern in KEEP_GLOB:
        for f in src.glob(pattern):
            if f.name in SKIP_NAMES:
                continue
            (dst / f.relative_to(src)).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst / f.relative_to(src))

    def git(*args: str) -> str:
        try:
            return subprocess.check_output(["git", "-C", str(src), *args], text=True).strip()
        except (OSError, subprocess.CalledProcessError):
            return "unknown"

    # newline="\n": the same bytes whichever OS runs the script
    with open(dst / "VENDORED.txt", "w", encoding="utf-8", newline="\n") as f:
        f.write(
            "Vendored Polycall core sources (build inputs for libpolycall only).\n"
            f"origin:  https://github.com/obinexus/polycall\n"
            f"commit:  {git('rev-parse', 'HEAD')}\n"
            f"describe: {git('describe', '--always', '--dirty')}\n"
            "Regenerate with: python scripts/vendor_core.py <path-to-polycall-checkout>\n"
        )
    print(f"vendored {src} -> {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
