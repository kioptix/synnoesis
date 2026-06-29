#!/usr/bin/env python3
"""keygen.py — generate an Ed25519 keypair for a Synnoesis mesh agent.

A thin CLI over ``sign.generate_keypair`` / ``sign._mesh_keys_dir`` — no new
crypto, no key derivation, just argparse + write + print. It mints a keypair,
writes the PRIVATE key born-local (under the data home, never printed and never
transmitted), and PRINTS the PUBLIC key so the operator can register it in a
peer's keyring (see ``keyring.py``).

Trust model: the private key is born on this machine and never leaves it. Only
the non-secret public key is shown. Registering that public key in another
agent's keyring is the deliberate trust decision (quickstart Section 5).

Usage:
  python comms/keygen.py --agent-id alice
  python comms/keygen.py --agent-id alice --force   # rotate an existing key

The private key lands at ``<PA_HOME>/mesh-keys/<agent-id>.key`` — exactly where
``sign.load_private_key`` looks for it on send.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Local mesh libs live beside this script (sys.path.insert + bare import) so this
# runs as a plain script without packaging the comms/ dir — same idiom as send.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sign  # noqa: E402


def cmd_keygen(args) -> int:
    if not sign.CRYPTO_AVAILABLE:
        print("ERROR: cryptography library not available - cannot keygen.\n"
              "Install it with: pip install cryptography",
              file=sys.stderr)
        return 2

    keys_dir = sign._mesh_keys_dir()
    keys_dir.mkdir(parents=True, exist_ok=True)
    key_path = keys_dir / f"{args.agent_id}.key"

    if key_path.exists() and not args.force:
        print(f"ERROR: a key already exists at {key_path}.\n"
              f"Refusing to overwrite - that would change {args.agent_id}'s "
              f"identity. Pass --force only if you really mean to rotate it.",
              file=sys.stderr)
        return 2

    priv_b64, pub_b64 = sign.generate_keypair()
    # Format matches sign.load_private_key (reads .strip()): one base64 line.
    key_path.write_text(priv_b64 + "\n", encoding="utf-8")
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass  # best-effort; Windows / odd filesystems may not honor chmod

    print(f"keypair generated for agent '{args.agent_id}'")
    print(f"  private key: {key_path}  (KEEP LOCAL - never transmit)")
    print(f"  public key : {pub_b64}")
    print()
    print("Next: register the PUBLIC key (safe to share) in a peer's keyring:")
    print(f"  python comms/keyring.py --add {args.agent_id} --pubkey {pub_b64}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="keygen",
        description="generate an Ed25519 keypair for a Synnoesis mesh agent "
                    "(private key born local, public key printed)")
    ap.add_argument("--agent-id", required=True,
                    help="the agent id this keypair belongs to")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing key (rotates the identity)")
    args = ap.parse_args(argv)
    return cmd_keygen(args)


if __name__ == "__main__":
    sys.exit(main())
