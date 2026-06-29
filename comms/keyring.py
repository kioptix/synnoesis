#!/usr/bin/env python3
"""keyring.py — register an agent's PUBLIC key in the Synnoesis mesh keyring.

A thin CLI over ``sign.load_keyring`` / ``sign._mesh_keys_dir`` — no crypto at
all, just argparse + load + set + write JSON. Adding (or rotating) a public key
in the keyring IS the trust decision: by recording ``alice``'s public key you
declare "I trust messages that alice signs" (quickstart Section 5). Nothing is
trusted by default; you choose, key by key, whom to believe.

The keyring is a plain, inspectable JSON file at
``<PA_HOME>/mesh-keyring.json`` (one level up from the mesh-keys dir) — exactly
where ``sign.load_keyring`` reads it on verify, so writer and reader agree on the
location. Public keys are non-secret and safe to share.

Usage:
  python comms/keyring.py --add alice --pubkey <base64-public-key>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Local mesh libs live beside this script (sys.path.insert + bare import) so this
# runs as a plain script without packaging the comms/ dir — same idiom as send.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sign  # noqa: E402


def _keyring_path() -> Path:
    """The keyring file location, resolved IDENTICALLY to sign.load_keyring()
    so this writer and the verify-side reader always agree on one path."""
    return sign._mesh_keys_dir().parent / "mesh-keyring.json"


def cmd_add(args) -> int:
    path = _keyring_path()
    # Reuse sign.load_keyring()'s reader: returns {"agents": {...}} (or an empty
    # ring if the file is absent/unreadable) - no parallel parse logic here.
    kr = sign.load_keyring()
    agents = kr.setdefault("agents", {})
    prior = agents.get(args.add)
    agents[args.add] = {
        "pubkey": args.pubkey,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "added_by": (os.environ.get("PA_AGENT_ID") or "").strip() or "local",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(kr, indent=2), encoding="utf-8")

    verb = "UPDATED (key rotated)" if prior else "registered"
    print(f"{args.add} {verb} in keyring: {path}")
    if prior:
        print(f"  previous pubkey: {str(prior.get('pubkey', '?'))[:16]}...")
    print(f"  current  pubkey: {args.pubkey[:16]}...")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="keyring",
        description="register an agent's PUBLIC key in the Synnoesis mesh "
                    "keyring (the trust decision)")
    ap.add_argument("--add", required=True, metavar="AGENT_ID",
                    help="the agent id whose public key you are registering")
    ap.add_argument("--pubkey", required=True, metavar="B64",
                    help="the agent's base64 Ed25519 public key")
    args = ap.parse_args(argv)
    return cmd_add(args)


if __name__ == "__main__":
    sys.exit(main())
