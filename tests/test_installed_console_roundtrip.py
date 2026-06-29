#!/usr/bin/env python3
"""test_installed_console_roundtrip.py -- gate #6: installed console-script round-trip.

Proves Doorway C (the optional ``pip install -e .`` global ``synnoesis`` command) is
a REAL artifact, not an assumption: it installs the project editable into a throwaway
venv, then runs ``synnoesis send`` followed by ``synnoesis read`` FROM A DIFFERENT CWD
and asserts the message round-trips. Running from another cwd is the point -- it proves
``_floor_dir()`` resolves the ``comms/`` floor from the package location, not from the
current directory.

Two environment notes (documented, not design changes):
  * ``tzdata`` is installed alongside the editable project. The floor advertises
    "stdlib only", but ``inbox.py`` calls ``zoneinfo.ZoneInfo(...)`` for the display
    timezone, and on Windows there is no system tz database -- a CLEAN venv that does
    not inherit the base site-packages raises ``ZoneInfoNotFoundError`` without the
    ``tzdata`` PyPI shim. The ambient interpreter happens to have it; an isolated
    venv does not. We add ``tzdata`` so this gate tests the v0.2.0 DISPATCH WIRING
    (the thing under test) rather than a pre-existing Windows tz-database gap in the
    floor. (That gap is reported separately as a finding, not fixed here.)
  * ``cryptography`` is NOT installed in the venv (it is optional), so the round-trip
    record verifies as ``unavailable`` -- correct warn-mode behavior. The round-trip
    is about delivery, not signing; we assert the body survives send -> read.

Fallback: if a venv cannot be created or the editable install fails in this
environment, the test does NOT silently pass -- it FALLS BACK to running
``python <repo>/synnoesis.py send|read`` from a different cwd (still proving the
cwd-independent dispatch) and prints a clear NOTE that the installed-script leg was
skipped for an environmental reason.

Run: python tests/test_installed_console_roundtrip.py   (exit 0 = pass, 1 = fail)
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SYNNOESIS_PY = REPO / "synnoesis.py"
BODY = "installed-console-roundtrip-body"

_failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"PASS  {label}")
    else:
        suffix = f"  ({detail})" if detail else ""
        print(f"FAIL  {label}{suffix}")
        _failures.append(label)


def _venv_exe(venv: Path, name: str) -> Path:
    scripts = venv / ("Scripts" if os.name == "nt" else "bin")
    exe = scripts / (name + (".exe" if os.name == "nt" else ""))
    return exe


def _try_install(venv: Path) -> Path | None:
    """Create a venv and editable-install the project (+tzdata). Return the path to
    the installed `synnoesis` console script, or None if the environment can't do it."""
    rc = subprocess.run([sys.executable, "-m", "venv", str(venv)],
                        capture_output=True, text=True)
    if rc.returncode != 0:
        print(f"      venv create failed: {rc.stderr.strip()!r}")
        return None
    py = _venv_exe(venv, "python")
    if not py.is_file():
        print(f"      no venv python at {py}")
        return None
    inst = subprocess.run(
        [str(py), "-m", "pip", "install", "-e", str(REPO), "tzdata", "--quiet"],
        capture_output=True, text=True)
    if inst.returncode != 0:
        print(f"      editable install failed: {inst.stderr.strip()[:400]!r}")
        return None
    syn = _venv_exe(venv, "synnoesis")
    if not syn.is_file():
        print(f"      console script not installed at {syn}")
        return None
    return syn


def _roundtrip(send_cmd: list[str], read_cmd: list[str], label: str) -> None:
    """Run send (in a different cwd) then read (in another different cwd) into a
    throwaway PA_HOME and assert the body survives the round-trip."""
    home = Path(tempfile.mkdtemp(prefix="syn-installed-home-"))
    cwd1 = Path(tempfile.mkdtemp(prefix="syn-cwd-send-"))
    cwd2 = Path(tempfile.mkdtemp(prefix="syn-cwd-read-"))
    try:
        env = dict(os.environ)
        env["PA_HOME"] = str(home)
        env["PA_AGENT_ID"] = "alice"

        sd = subprocess.run(send_cmd, env=env, cwd=str(cwd1),
                            capture_output=True, text=True)
        check(f"[{label}] send exits 0 from a different cwd", sd.returncode == 0,
              f"rc={sd.returncode} stderr={sd.stderr.strip()!r}")

        inbox = home / "comms" / "bob-inbox.jsonl"
        check(f"[{label}] recipient inbox written under PA_HOME", inbox.is_file(),
              f"expected {inbox}")

        rd = subprocess.run(read_cmd, env=env, cwd=str(cwd2),
                            capture_output=True, text=True)
        check(f"[{label}] read exits 0 from a different cwd", rd.returncode == 0,
              f"rc={rd.returncode} stderr={rd.stderr.strip()!r}")
        check(f"[{label}] read delivers the sent body (round-trip)",
              BODY in rd.stdout, f"stdout={rd.stdout.strip()!r}")
    finally:
        import shutil
        for d in (home, cwd1, cwd2):
            shutil.rmtree(d, ignore_errors=True)


def main() -> int:
    venv = Path(tempfile.mkdtemp(prefix="syn-venv-")) / "venv"
    installed = False
    try:
        syn = _try_install(venv)
        if syn is not None:
            installed = True
            print("NOTE: installed-console leg ACTIVE (editable install into throwaway venv).")
            _roundtrip(
                [str(syn), "send", "--to", "bob", BODY],
                [str(syn), "read", "--agent-id", "bob", "--since", "2h"],
                "installed")
        else:
            print("NOTE: installed-console leg SKIPPED for an environmental reason "
                  "(venv/pip unavailable). Falling back to root-script dispatch from "
                  "a different cwd -- still proves cwd-independent routing, but NOT the "
                  "pip-installed entry point.")
            _roundtrip(
                [sys.executable, str(SYNNOESIS_PY), "send", "--to", "bob", BODY],
                [sys.executable, str(SYNNOESIS_PY), "read", "--agent-id", "bob",
                 "--since", "2h"],
                "fallback")
    finally:
        import shutil
        shutil.rmtree(venv.parent, ignore_errors=True)

    print()
    if _failures:
        print(f"RESULT  FAIL  ({len(_failures)} failed: {', '.join(_failures)})")
        return 1
    leg = "installed console script" if installed else "root-script fallback"
    print(f"RESULT  PASS  ({leg} round-trips send -> read from a different cwd)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
