"""pa_paths.py — portable path resolution for Synnoesis services.

Single source of truth for "where does service X keep its data". Kills the
hardcoded absolute paths that make the system non-portable (the OSS rule: NO
absolute path baked into the code — only env/config-driven or container-internal).

It also reconciles a common inconsistency: one launcher may default a service's
data dir to an absolute Windows path while a library falls back to an absolute
Linux path for the SAME directory. After services resolve through here, there is
exactly one answer, derived the same way everywhere.

Resolution order — most specific first:
  1. ``PA_<SERVICE>_DIR``      explicit per-service override (e.g. PA_MEMORY_DIR).
                               Matches the env names the launchers already use, so
                               existing deployments that set them keep working.
  2. ``PA_HOME``/<service>     one data root for everything. Set ``PA_HOME=/data``
                               in a container (controlled absolute path = allowed),
                               or ``PA_HOME=~/synnoesis`` on a host.
  3. ``<home>/.synnoesis/<service>``  zero-config default. ``Path.home()`` is
                               OS-resolved, so NO absolute path is hardcoded and a
                               fresh checkout works on Windows/macOS/Linux alike.

Always returns an absolute, ``~``-expanded ``Path``. Does not create the directory
unless ``create=True`` is passed.
"""
from __future__ import annotations

import os
from pathlib import Path

APP_DIRNAME = ".synnoesis"   # zero-config data root under the user's home


def _env(name: str) -> str | None:
    """An env var's value, or None if unset/blank."""
    v = os.environ.get(name)
    return v.strip() if v and v.strip() else None


def _norm(service: str) -> str:
    """Normalize a service name for the dir + env-var ('pa-memory' -> 'memory')."""
    s = service.strip().lower()
    return s[3:] if s.startswith("pa-") or s.startswith("pa_") else s


def pa_home() -> Path:
    """The shared data root: ``PA_HOME`` if set, else ``~/.synnoesis``."""
    home = _env("PA_HOME")
    base = Path(home).expanduser() if home else (Path.home() / APP_DIRNAME)
    return base.resolve()


def service_dir(service: str, *, create: bool = False) -> Path:
    """Resolve the data directory for ``service`` (e.g. 'memory', 'dayplan').

    Honors a ``PA_<SERVICE>_DIR`` override first, then ``PA_HOME``/<service>,
    then ``~/.synnoesis/<service>``. With ``create=True`` the directory is
    created (parents included) if missing.
    """
    name = _norm(service)
    per = _env(f"PA_{name.upper()}_DIR")
    if per:
        path = Path(per).expanduser().resolve()
    else:
        path = (pa_home() / name).resolve()
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def service_db(service: str, filename: str, *, create_dir: bool = False) -> Path:
    """Path to a file (e.g. a sqlite db) inside a service's data dir."""
    return service_dir(service, create=create_dir) / filename
