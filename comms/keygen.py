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


def _restrict_perms(key_path: Path) -> None:
    """Restrict the born-local private key to owner-only (chmod 0o600). On
    Windows (and any filesystem that ignores POSIX permission bits) chmod cannot
    remove group/other access, so rather than leave the operator falsely assured
    the key is locked down, WARN accurately. The key still works; confirming its
    at-rest permission via the user-account directory ACL is then the operator's
    call."""
    honored = False
    try:
        os.chmod(key_path, 0o600)
        # On 'nt', chmod only toggles the read-only bit -- it does NOT drop
        # group/other read -- so a 0o600 request is effectively a no-op for
        # confidentiality. Treat that as "not honored" and warn.
        honored = (os.name != "nt")
    except OSError:
        honored = False
    if not honored:
        print(
            "WARNING: could not restrict permissions on the private key file:\n"
            f"  {key_path}\n"
            "  chmod 0o600 is not honored on this OS/filesystem (e.g. Windows).\n"
            "  The key now relies on your user-account directory ACL for "
            "protection;\n"
            "  verify this file is not readable by other users.",
            file=sys.stderr)


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
    _restrict_perms(key_path)

    fp = sign.pubkey_fingerprint(pub_b64)
    print(f"keypair generated for agent '{args.agent_id}'")
    print(f"  private key: {key_path}  (KEEP LOCAL - never transmit)")
    print(f"  public key : {pub_b64}")
    print(f"  fingerprint: {fp}")
    print()
    print("Next: share the PUBLIC key (safe to share) AND read the FINGERPRINT to")
    print("your peer over a trusted channel. They verify it matches, then pin it:")
    print(f"  python comms/keyring.py --add {args.agent_id} --pubkey {pub_b64} "
          f"--expect-fingerprint {fp}")
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
