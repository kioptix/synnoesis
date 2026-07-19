#!/usr/bin/env python3
"""release-gate.py -- run before tagging or publishing. Exits non-zero on failure.

    python tools/release-gate.py

WHY THIS EXISTS
---------------
v0.4.0 was one command away from being tagged with artifacts that did not contain
the code. `packages = ["synnoesis"]` shipped three files; the entire comms/ floor
was absent from BOTH the wheel and the sdist. `pip install synnoesis` produced a
CLI where every real subcommand raised ImportError.

It survived because **every path we develop on works**. Checkout works. Editable
install works. The test suite passes. Only the path we never use -- a real
install of a built artifact -- was broken, and nothing exercised it.

    A RELEASE GATE MUST EXERCISE THE ARTIFACT IN THE ENVIRONMENT THE
    DEVELOPER NEVER USES.

That is this script's entire job. It is deliberately a script and not a checklist
line: a checklist is a convention and conventions drift; a gate holds.

TWO TRAPS IT IS BUILT AROUND
----------------------------
1. `--help` PASSES ON A DEAD PACKAGE. It is handled by cli.py before the floor is
   ever resolved, so it proves nothing. The gate runs `doctor`, a REAL subcommand
   that forces floor resolution.
2. FOUR WORKING COMMANDS DO NOT PROVE THE ARTIFACT IS COMPLETE. They prove THEIR
   imports resolve. A module none of them touch can still be missing, so the gate
   also compares the wheel's contents against comms/ EXHAUSTIVELY.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REAL_SUBCOMMAND = ["doctor"]          # NOT --help: it must force floor resolution
failures: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)
    print(f"  FAIL  {msg}")


def ok(msg: str) -> None:
    print(f"  ok    {msg}")


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def check_version_consistency() -> None:
    """Run the in-tree version gate FIRST -- it is cheap and it fails fast.

    The check itself already existed (tests/test_version_consistency.py) but
    NOTHING RAN IT at release time, which is its own trap: a check that exists
    but is never invoked reads like coverage and provides none. Wired, not fired.

    Runs first because a version mismatch invalidates every artifact built after
    it -- no point spending two clean-venv installs on a build that is mislabeled.
    """
    print("\n[1/5] version consistency across in-tree sites")
    test = REPO / "tests" / "test_version_consistency.py"
    if not test.is_file():
        # Fail-closed: a MISSING gate is not a passing gate.
        fail(f"version gate missing: {test.relative_to(REPO)} not found")
        return
    r = run([sys.executable, str(test)], cwd=str(REPO))
    if r.returncode != 0:
        blob = (r.stdout or "") + (r.stderr or "")
        bad = [l.strip() for l in blob.splitlines() if l.strip().startswith("FAIL")]
        fail("version sites disagree or are stale: "
             + ("; ".join(bad)[:300] if bad else blob.strip()[-200:]))
    else:
        ok("all in-tree version sites agree")


def build_artifacts(outdir: Path) -> tuple[Path | None, Path | None]:
    print("\n[2/5] building wheel + sdist")
    # cwd OUTSIDE the repo, for the same reason check_install does it: run from the
    # repo root and the repo's own `build/` artifact directory shadows the `build`
    # TOOL as a namespace package, so `python -m build` dies with "'build' is a
    # package and cannot be directly executed" and we silently drop to the
    # wheel-only fallback. Observed on a tree that had been built in-place; it also
    # makes `import build` succeed while the tool is not installed at all, which is
    # a very convincing false positive. Passing REPO explicitly keeps the target
    # unambiguous from anywhere.
    r = run([sys.executable, "-m", "build", "--outdir", str(outdir), str(REPO)],
            cwd=str(outdir))
    if r.returncode != 0:
        # `build` is the modern backend-agnostic builder; without it we can still
        # wheel, but an sdist-less run must NOT silently pass -- it is half a gate.
        why = (r.stderr or r.stdout or "").strip().splitlines()
        print(f"        `python -m build` failed: {why[-1][:120] if why else '?'}")
        print("        falling back to pip wheel")
        r2 = run([sys.executable, "-m", "pip", "wheel", str(REPO),
                  "--no-deps", "-w", str(outdir)])
        if r2.returncode != 0:
            fail(f"could not build any artifact: {r2.stderr.strip()[:200]}")
            return None, None
        fail("SDIST NOT BUILT (`pip install build` to close this gap) -- "
             "source installs are a real user path and are UNVERIFIED")
    whl = next(iter(glob.glob(str(outdir / "*.whl"))), None)
    sdist = next(iter(glob.glob(str(outdir / "*.tar.gz"))), None)
    ok(f"wheel : {Path(whl).name}" if whl else "wheel : MISSING")
    if not whl:
        fail("no wheel produced")
    if sdist:
        ok(f"sdist : {Path(sdist).name}")
    return (Path(whl) if whl else None, Path(sdist) if sdist else None)


def check_wheel_complete(whl: Path) -> None:
    """EXHAUSTIVE: every comms/*.py must be in the wheel. Command-sampling samples."""
    print("\n[3/5] wheel contents vs comms/ (exhaustive, not sampled)")
    repo_mods = {p.name for p in (REPO / "comms").glob("*.py")}
    names = zipfile.ZipFile(whl).namelist()
    shipped = {os.path.basename(n) for n in names
               if "/comms/" in n and n.endswith(".py")}
    missing = sorted(repo_mods - shipped)
    if missing:
        fail(f"{len(missing)} module(s) missing from the wheel: {missing}")
    else:
        ok(f"all {len(repo_mods)} comms modules present")


def check_install(artifact: Path, label: str) -> None:
    """Install into a CLEAN venv and run a REAL subcommand -- the developer-never-uses path."""
    print(f"\n[{'4' if label == 'wheel' else '5'}/5] clean-venv install from {label}")
    with tempfile.TemporaryDirectory() as td:
        venv = Path(td) / "v"
        if run([sys.executable, "-m", "venv", str(venv)]).returncode != 0:
            fail(f"{label}: could not create venv")
            return
        py = venv / ("Scripts" if os.name == "nt" else "bin") / "python"
        r = run([str(py), "-m", "pip", "install", "--quiet", str(artifact)])
        if r.returncode != 0:
            fail(f"{label}: install failed: {r.stderr.strip()[:200]}")
            return
        ok(f"{label}: installed")
        exe = venv / ("Scripts" if os.name == "nt" else "bin") / "synnoesis"
        cmd = [str(exe), *REAL_SUBCOMMAND] if (exe.exists() or os.name == "nt") \
            else [str(py), "-m", "synnoesis", *REAL_SUBCOMMAND]
        # cwd OUTSIDE the repo: from inside it, a stray sibling comms/ could mask
        # a broken install and hand us a false pass.
        r = run(cmd, cwd=td)
        blob = (r.stdout or "") + (r.stderr or "")
        if "Traceback" in blob or r.returncode != 0:
            last = [l for l in blob.strip().splitlines() if l.strip()]
            fail(f"{label}: `synnoesis {' '.join(REAL_SUBCOMMAND)}` failed "
                 f"-> {last[-1][:160] if last else 'no output'}")
        else:
            ok(f"{label}: `synnoesis {' '.join(REAL_SUBCOMMAND)}` runs")


def main() -> int:
    print(f"release gate -- {REPO.name}")
    print("exercising the artifact in the environment the developer never uses")
    check_version_consistency()
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "dist"
        out.mkdir()
        whl, sdist = build_artifacts(out)
        if whl:
            check_wheel_complete(whl)
            check_install(whl, "wheel")
        if sdist:
            check_install(sdist, "sdist")
    print()
    if failures:
        print(f"GATE FAILED -- {len(failures)} problem(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("GATE PASSED -- both artifacts install clean and run a real subcommand.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
