#!/usr/bin/env python3
"""doctor.py — print Synnoesis's resolved config so silent drift is visible.

The #1 "the mesh is broken" footgun is ``PA_HOME`` / ``PA_AGENT_ID`` drifting
between shells: a fresh terminal that never re-exported them falls back to
``~/.synnoesis`` (a DIFFERENT keyring), so keys look missing and every peer
reads as ``no-key`` with no error pointing at the cause. This prints exactly what
the tools resolved — one command a confused user runs first to see where their
keys/keyring actually live and whether signing is even possible.

Pure stdlib; reports ``cryptography`` availability rather than requiring it.

Usage:
  python comms/doctor.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Local mesh libs live beside this script (sys.path.insert + bare import).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402  — portable data-home resolver
import sign   # noqa: E402  — key dir / keyring / crypto availability


def main(argv=None) -> int:
    home = paths.pa_home()
    from_env = bool((os.environ.get("PA_HOME") or "").strip())
    agent = (os.environ.get("PA_AGENT_ID") or "").strip()
    keys_dir = sign._mesh_keys_dir()
    keyring_path = keys_dir.parent / "mesh-keyring.json"
    try:
        n = len(sign.load_keyring_strict().get("agents") or {})
        keyring_state = f"{n} agent{'' if n == 1 else 's'}"
    except ValueError:
        keyring_state = "UNREADABLE/CORRUPT — back up and inspect this file"

    src = ("from env PA_HOME" if from_env
           else "default (~/.synnoesis); set PA_HOME to pin")
    print("Synnoesis doctor")
    print(f"  PA_HOME      : {home}  ({src})")
    print(f"  agent id     : {agent or '(unset — set PA_AGENT_ID)'}")
    if agent:
        priv = keys_dir / f"{agent}.key"
        print(f"  private key  : {'present' if priv.is_file() else 'ABSENT'}  "
              f"({priv})")
    print(f"  keyring      : {keyring_path}  ({keyring_state})")
    if sign.CRYPTO_AVAILABLE:
        print("  cryptography : available (signing + verification enabled)")
    else:
        print("  cryptography : MISSING — signing/verification disabled "
              "(warn-mode only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
