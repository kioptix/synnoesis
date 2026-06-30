#!/usr/bin/env python3
"""test_single_module_identity.py -- gate #5: one floor module identity, no twin.

The one-writer guarantee ("``wire.wrap_outer`` is the SINGLE outer-record builder")
depends on the floor modules existing as ONE module identity. If the dispatcher ever
imported the floor as a package (``import comms.wire``) while the scripts import it
bare (``import wire``), there would be TWO module objects -- two copies of
module-level state (e.g. ``sign.CRYPTO_AVAILABLE``, function ``is``-identity) --
defeating the guarantee. The spec pins every doorway to bare ``import send`` /
``import inbox`` via the ``_floor_dir()`` path-insert, NEVER as ``comms.*``.

This gate runs a real installed-STYLE dispatch -- it imports ``synnoesis.cli`` (the
package module, the way an installed console-script entry would) and calls
``main(["send", ...])`` -- then inspects ``sys.modules`` and asserts:
  * ``wire`` / ``sign`` / ``paths`` are each present exactly once (bare name); and
  * NO ``comms.wire`` / ``comms.sign`` / ``comms.paths`` twin was created.

We import via the package path (``from synnoesis.cli import main``) precisely so the
test exercises the package-import entry, not the bare root-script path -- that's
where a ``comms.*`` twin would sneak in if the dispatch resolved the floor as a
package. The send targets a throwaway PA_HOME so it does no real I/O of consequence.

Run: python tests/test_single_module_identity.py   (exit 0 = pass, 1 = fail)
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Driver run in a FRESH interpreter so sys.modules starts clean (this test file
# itself imports nothing from the floor). It performs the package-entry dispatch
# and prints a verdict line we parse back.
_DRIVER = r'''
import os, sys, json
REPO = r"%(repo)s"
sys.path.insert(0, REPO)  # make the `synnoesis` package importable, installed-style

# Installed-style entry: import the package's cli and call main() with sliced argv.
from synnoesis.cli import main
rc = main(["send", "--to", "bob", "identity-probe"])

twins = sorted(m for m in sys.modules
               if m in ("comms.wire", "comms.sign", "comms.paths",
                        "comms.send", "comms.inbox", "comms"))
bare = {name: (name in sys.modules) for name in ("wire", "sign", "paths",
                                                 "send", "inbox")}
print("RC", rc)
print("BARE", json.dumps(bare))
print("TWINS", json.dumps(twins))
'''

_failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"PASS  {label}")
    else:
        suffix = f"  ({detail})" if detail else ""
        print(f"FAIL  {label}{suffix}")
        _failures.append(label)


def main() -> int:
    home = Path(tempfile.mkdtemp(prefix="syn-identity-"))
    try:
        env = dict(os.environ)
        env["PA_HOME"] = str(home)
        env["PA_AGENT_ID"] = "alice"
        proc = subprocess.run(
            [sys.executable, "-c", _DRIVER % {"repo": str(REPO)}],
            env=env, capture_output=True, text=True)

        if proc.returncode != 0:
            check("driver ran cleanly", False,
                  f"rc={proc.returncode} stderr={proc.stderr.strip()!r}")
            return 1

        out = proc.stdout
        import json as _json
        bare = {}
        twins = []
        for line in out.splitlines():
            if line.startswith("BARE "):
                bare = _json.loads(line[len("BARE "):])
            elif line.startswith("TWINS "):
                twins = _json.loads(line[len("TWINS "):])

        # The floor modules the dispatch actually touches must be present as the
        # bare name (proves the path-insert + bare import worked).
        for name in ("wire", "sign", "paths"):
            check(f"bare module {name!r} present after dispatch", bare.get(name),
                  f"bare={bare}")

        # The headline: NO comms.* twin anywhere in sys.modules.
        check("NO comms.* floor twin in sys.modules",
              twins == [],
              f"found twins={twins}")
    finally:
        import shutil
        shutil.rmtree(home, ignore_errors=True)

    print()
    if _failures:
        print(f"RESULT  FAIL  ({len(_failures)} failed: {', '.join(_failures)})")
        return 1
    print("RESULT  PASS  (one floor module identity; no comms.* twin under package dispatch)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
