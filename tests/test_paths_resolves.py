"""test_paths_resolves.py — portability checks for comms/paths.py.

Self-running: `python tests/test_paths_resolves.py` (exit 0 = pass, 1 = fail).
The test_* functions also follow pytest naming so the file can be collected by
pytest where it is installed (no pytest-only fixtures are used).

Covers the OSS portability rules paths.py exists to enforce:
  1. PA_HOME with a SPACE in the path resolves correctly (Path-based, no
     shell-escaping / string-concat bug).
  2. PA_<SERVICE>_DIR override wins over PA_HOME.
  3. The resolved path DERIVES from env — it is not a hardcoded C:/ absolute.

Each test isolates os.environ so cases don't leak into each other, and uses a
real temp dir so Path.resolve() reflects the actual filesystem.
"""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

# Import comms/paths.py from the repo root regardless of the cwd / clone path
# (a space in the clone path must not break import resolution either).
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from comms import paths  # noqa: E402


@contextmanager
def clean_pa_env(**overrides: str):
    """Run with a PA_*-free environment plus the given overrides, then restore.

    Strips every PA_* var so an ambient PA_HOME / PA_COMMS_DIR on the dev box
    can't mask a real bug, then applies only what the test sets.
    """
    saved = {k: v for k, v in os.environ.items() if k.startswith("PA_")}
    for k in saved:
        del os.environ[k]
    os.environ.update(overrides)
    try:
        yield
    finally:
        for k in list(os.environ):
            if k.startswith("PA_"):
                del os.environ[k]
        os.environ.update(saved)


def test_pa_home_with_space_resolves_under_home():
    """(1) PA_HOME whose path CONTAINS A SPACE -> service_dir lives under it."""
    with tempfile.TemporaryDirectory(prefix="syn home ") as raw_tmp:
        # Force a space into the path component even if the OS temp root has none.
        home = Path(raw_tmp) / "PA HOME with space"
        home.mkdir(parents=True, exist_ok=True)
        assert " " in str(home), "test setup failed: no space in PA_HOME path"

        with clean_pa_env(PA_HOME=str(home)):
            resolved = paths.service_dir("comms")

        expected = (home / "comms").resolve()
        assert resolved == expected, f"{resolved!r} != {expected!r}"
        # The space survived intact — no truncation/escaping corruption.
        assert resolved.parent == home.resolve(), (
            f"parent {resolved.parent!r} is not the space-containing PA_HOME "
            f"{home.resolve()!r}"
        )
        assert resolved.name == "comms"
        print(f"[1] PA_HOME w/ space -> {resolved}  OK")


def test_per_service_override_wins():
    """(2) PA_COMMS_DIR override beats PA_HOME for the comms service."""
    with tempfile.TemporaryDirectory(prefix="syn home ") as raw_home, \
            tempfile.TemporaryDirectory(prefix="syn override ") as raw_override:
        home = Path(raw_home) / "PA HOME"
        override = Path(raw_override) / "EXPLICIT comms dir"
        home.mkdir(parents=True, exist_ok=True)
        override.mkdir(parents=True, exist_ok=True)

        with clean_pa_env(PA_HOME=str(home), PA_COMMS_DIR=str(override)):
            resolved = paths.service_dir("comms")

        assert resolved == override.resolve(), (
            f"override did not win: {resolved!r} != {override.resolve()!r}"
        )
        # And it specifically did NOT fall through to PA_HOME/comms.
        assert resolved != (home / "comms").resolve(), (
            "override ignored — resolved under PA_HOME instead"
        )
        print(f"[2] PA_COMMS_DIR override wins -> {resolved}  OK")


def test_path_is_env_derived_not_hardcoded():
    """(3) The resolved path derives from env, not a baked-in C:/ absolute.

    Point PA_HOME at a temp dir and prove the resolved comms dir is *inside*
    that temp dir. If anything were hardcoded to an absolute dev path, it could
    not be a child of an arbitrary fresh temp root.
    """
    with tempfile.TemporaryDirectory(prefix="syn envderived ") as raw_tmp:
        home = Path(raw_tmp).resolve()
        with clean_pa_env(PA_HOME=str(home)):
            resolved = paths.service_dir("comms")

        # Derives from the env-supplied root: it is under our temp PA_HOME.
        assert str(resolved).startswith(str(home)), (
            f"{resolved!r} is not under env-supplied PA_HOME {home!r}"
        )
        # Not a well-known hardcoded dev absolute.
        bad = Path("C:/some/hardcoded/dev/path").resolve()
        assert bad not in resolved.parents and resolved != bad, (
            f"resolved path looks hardcoded under {bad!r}: {resolved!r}"
        )
        print(f"[3] env-derived, not hardcoded -> {resolved}  OK")


def _run_all() -> int:
    tests = [
        test_pa_home_with_space_resolves_under_home,
        test_per_service_override_wins,
        test_path_is_env_derived_not_hardcoded,
    ]
    failures = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failures += 1
            print(f"FAIL: {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001 - surface any setup/import error
            failures += 1
            print(f"ERROR: {t.__name__}: {type(e).__name__}: {e}")
    total = len(tests)
    print(f"\n{total - failures}/{total} passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
