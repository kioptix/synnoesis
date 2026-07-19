#!/usr/bin/env python3
"""test_version_consistency.py -- gate #7: every version site reads one version, atomically.

Version drift across the metadata files (the bug an earlier hand-fix had to correct)
is caught here so a release can never ship mismatched versions. Three file-based
version sites must all read the SAME string AND that string must be the expected
release version (``EXPECTED`` below):

  * ``pyproject.toml``            -> [project] version
  * ``synnoesis/__init__.py``    -> __version__
  * ``CHANGELOG.md``             -> the latest released ``## [x.y.z]`` heading

``package.json`` was a fourth site until 0.4.1, when the npm placeholder was
removed: it shipped a 5-line ``index.js`` exporting ``{}`` while carrying the same
version as the real Python package, claiming a parity that did not exist. If an
npm package is ever published for real, add it back here in the same breath.

The git tag (the 5th site in the spec) is created by the human at release time under
ROE-1 (agents never tag/commit Synnoesis), so it is intentionally NOT asserted here;
this gate covers the four in-tree sources. We parse each with stdlib only (a tiny
regex / json), never hand-typing the expected number anywhere but the single
``EXPECTED`` constant -- and we assert MUTUAL equality too, so a future bump that
misses one file fails even if EXPECTED were forgotten.

Run: python tests/test_version_consistency.py   (exit 0 = pass, 1 = fail)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXPECTED = "0.4.1"

_failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"PASS  {label}")
    else:
        suffix = f"  ({detail})" if detail else ""
        print(f"FAIL  {label}{suffix}")
        _failures.append(label)


def _pyproject_version() -> str | None:
    txt = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    # [project] table version. Match a `version = "x.y.z"` line; tolerate the
    # [build-system] requires line by anchoring on the key at line start.
    m = re.search(r'(?m)^\s*version\s*=\s*"([^"]+)"', txt)
    return m.group(1) if m else None


def _init_version() -> str | None:
    txt = (REPO / "synnoesis" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'(?m)^__version__\s*=\s*"([^"]+)"', txt)
    return m.group(1) if m else None


def _changelog_version() -> str | None:
    txt = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    # First released heading: "## [x.y.z]" -- skip an "[Unreleased]" heading.
    for m in re.finditer(r'(?m)^##\s*\[([^\]]+)\]', txt):
        tag = m.group(1).strip()
        if tag.lower() == "unreleased":
            continue
        return tag
    return None


def main() -> int:
    sites = {
        "pyproject.toml": _pyproject_version(),
        "synnoesis/__init__.py": _init_version(),
        "CHANGELOG.md (latest released heading)": _changelog_version(),
    }

    for name, ver in sites.items():
        check(f"{name} parsed a version", ver is not None,
              "could not extract a version string")
        check(f"{name} == {EXPECTED}", ver == EXPECTED,
              f"got {ver!r}")

    # Mutual equality across all sites that parsed -- catches a partial bump even
    # if EXPECTED were stale.
    parsed = [v for v in sites.values() if v is not None]
    check("all version sites agree with each other",
          len(set(parsed)) == 1 and len(parsed) == len(sites),
          f"distinct={sorted(set(parsed))}  parsed {len(parsed)}/{len(sites)} sites")

    print()
    if _failures:
        print(f"RESULT  FAIL  ({len(_failures)} failed: {', '.join(_failures)})")
        return 1
    print(f"RESULT  PASS  (all 3 in-tree version sites read {EXPECTED}; git tag is human/ROE-1)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
