#!/usr/bin/env python3
"""fingerprint.py — print a Synnoesis public-key fingerprint for OOB comparison.

The fingerprint is ``synnoesis-fp:<hex sha256 of the raw 32-byte Ed25519 pubkey>``
(see ``sign.pubkey_fingerprint``). Two agents compute it identically, so reading
it aloud / comparing it over a trusted channel confirms they hold the SAME key
before one pins the other (``keyring.py --add … --expect-fingerprint``). It is
pure stdlib (hashlib), so it works even when ``cryptography`` is not installed.

It is deliberately NOT ``ssh-keygen -lf`` compatible (OpenSSH hashes the
ssh-ed25519 wire blob); the only requirement is that two Synnoesis agents agree.

Usage:
  python comms/fingerprint.py --agent-id alice     # fp of alice's PINNED key
  python comms/fingerprint.py --pubkey <b64>       # fp of a raw public key
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Local mesh libs live beside this script (sys.path.insert + bare import).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sign  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="fingerprint",
        description="print a Synnoesis public-key fingerprint for OOB compare")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--agent-id", metavar="AGENT_ID",
                   help="fingerprint this agent's PINNED key from the keyring")
    g.add_argument("--pubkey", metavar="B64",
                   help="fingerprint a raw base64 public key")
    args = ap.parse_args(argv)

    if args.pubkey:
        try:
            print(sign.pubkey_fingerprint(args.pubkey))
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        return 0

    entry = (sign.load_keyring().get("agents") or {}).get(args.agent_id)
    if not entry or not entry.get("pubkey"):
        print(f"ERROR: '{args.agent_id}' is not in the keyring "
              "(nothing to fingerprint)", file=sys.stderr)
        return 2
    try:
        fp = sign.pubkey_fingerprint(entry["pubkey"])
    except ValueError as e:
        print(f"ERROR: '{args.agent_id}' has an invalid pinned key: {e}",
              file=sys.stderr)
        return 2
    print(f"{args.agent_id}  {fp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
