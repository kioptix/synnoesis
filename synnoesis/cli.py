"""synnoesis console dispatcher.

Single source of truth for subcommand routing. The root ``synnoesis.py``
script, ``python -m synnoesis``, and the ``[project.scripts] synnoesis``
console command are all thin shims that call ``main()`` here -- the dispatch
body is written ONCE.

Delegation passes the sliced argv as a parameter to the floor mains
(``send.main(rest)`` / ``inbox.main(rest)``); it never mutates ``sys.argv``.
Each floor main keeps owning its own argparse, so every existing flag is
inherited for free and stays the single source of truth.
"""

import sys
from pathlib import Path


def _floor_dir() -> Path:
    """Resolve the comms/ floor dir. Under both the zero-install checkout and the
    b2 editable install, the floor sits at <repo>/comms beside synnoesis/."""
    return Path(__file__).resolve().parent.parent / "comms"


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    sys.path.insert(0, str(_floor_dir()))     # ONE bootstrap, here only
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: synnoesis "
              "{send|read|keygen|keyring|fingerprint|doctor} [args...]")
        return 0 if argv else 2
    sub, rest = argv[0], argv[1:]
    if sub == "send":
        import send
        return send.main(rest)
    if sub in ("read", "inbox"):
        import inbox
        return inbox.main(rest)
    if sub == "keygen":
        import keygen
        return keygen.main(rest)
    if sub == "keyring":
        import keyring
        return keyring.main(rest)
    if sub == "fingerprint":
        import fingerprint
        return fingerprint.main(rest)
    if sub == "doctor":
        import doctor
        return doctor.main(rest)
    print(f"unknown subcommand {sub!r}; expected "
          "send|read|keygen|keyring|fingerprint|doctor", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
