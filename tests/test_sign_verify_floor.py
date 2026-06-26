#!/usr/bin/env python3
"""test_sign_verify_floor.py — self-running floor test for comms/sign.py.

Exercises the full sign -> verify_against_keyring contract end to end:

  1. generate_keypair() mints an Ed25519 keypair.
  2. The private key is written to a temp mesh-keys dir as <agent>.key and the
     public key into a mesh-keyring.json one level up — the EXACT layout
     load_private_key() / load_keyring() expect (see sign._mesh_keys_dir).
  3. new_message() signs (it calls load_private_key under the hood, so this
     proves the on-disk key is found and used).
  4. verify_against_keyring() returns each of the four reachable statuses:
       "ok"        — untouched signed message
       "bad"       — body tampered after signing
       "unsigned"  — _sig dropped
       "no-key"    — sender absent from the keyring (empty ring)

sign._mesh_keys_dir() has NO env override (it resolves to <repo>/state or
~/.pa) — so rather than write keys into the real working tree, we point it at
a throwaway TemporaryDirectory by reassigning sign._mesh_keys_dir for the run.
load_private_key() and load_keyring() both go through that one function, so a
single redirect captures both the key dir (<tmp>/mesh-keys) and the keyring
path (<tmp>/mesh-keyring.json, its parent).

If `cryptography` is unavailable the module degrades (CRYPTO_AVAILABLE=False):
we print SKIP and exit 0 rather than fail — a missing optional dep is not a
test failure.

Run directly:  python tests/test_sign_verify_floor.py
Exit 0 = pass or skip; exit 1 = an assertion failed.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# Make comms/ importable no matter the CWD or where the repo is cloned
# (a space in the clone path is fine — pathlib handles it).
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "comms"))

import sign  # noqa: E402  (path set up just above)


def main() -> int:
    if not sign.CRYPTO_AVAILABLE:
        print("SKIP: cryptography library not available; sign.py degraded "
              "(CRYPTO_AVAILABLE=False). Nothing to verify.")
        return 0

    agent = "alice-test"
    peer = "bob-test"

    with tempfile.TemporaryDirectory(prefix="synnoesis-sign-floor-") as td:
        tmp = Path(td)
        keys_dir = tmp / "mesh-keys"
        keys_dir.mkdir(parents=True, exist_ok=True)
        keyring_path = tmp / "mesh-keyring.json"  # _mesh_keys_dir().parent

        # 1. fresh keypair
        priv_b64, pub_b64 = sign.generate_keypair()

        # 2. lay it out exactly as load_private_key / load_keyring expect:
        #    <mesh-keys>/<agent>.key  +  <mesh-keys>/../mesh-keyring.json
        (keys_dir / f"{agent}.key").write_text(priv_b64, encoding="utf-8")
        keyring = {"agents": {agent: {"pubkey": pub_b64}}}
        keyring_path.write_text(json.dumps(keyring, indent=2), encoding="utf-8")

        # Redirect sign's hardcoded resolver at our temp tree so the REAL
        # load_private_key / load_keyring read from here (no env hook exists).
        orig_resolver = sign._mesh_keys_dir
        sign._mesh_keys_dir = lambda: keys_dir
        try:
            # sanity: the redirect actually wired both loaders to the temp tree
            assert sign.load_private_key(agent) == priv_b64, \
                "load_private_key did not read the temp key"
            loaded_ring = sign.load_keyring()
            assert loaded_ring == keyring, \
                f"load_keyring mismatch: {loaded_ring!r}"

            # 3. new_message() signs using the on-disk private key
            msg = sign.new_message(agent, peer, "floor-test body", "normal")
            assert msg.get("_sig"), "new_message produced no signature"
            assert msg.get("_from") == agent and msg.get("_to") == peer

            ring = sign.load_keyring()

            # 4a. untouched -> ok
            status, detail = sign.verify_against_keyring(msg, ring)
            assert status == "ok", f"expected ok, got {status!r} ({detail})"
            print(f"  ok       -> {status}: {detail}")

            # 4b. tamper the body -> bad (sig no longer matches canonical bytes)
            tampered = dict(msg)
            tampered["body"] = "floor-test body TAMPERED"
            status, detail = sign.verify_against_keyring(tampered, ring)
            assert status == "bad", f"expected bad, got {status!r} ({detail})"
            print(f"  tamper   -> {status}: {detail}")

            # 4c. drop _sig -> unsigned
            unsigned = {k: v for k, v in msg.items() if k != "_sig"}
            status, detail = sign.verify_against_keyring(unsigned, ring)
            assert status == "unsigned", \
                f"expected unsigned, got {status!r} ({detail})"
            print(f"  unsigned -> {status}: {detail}")

            # 4d. empty keyring -> no-key (signed, but sender unknown)
            status, detail = sign.verify_against_keyring(msg, {"agents": {}})
            assert status == "no-key", \
                f"expected no-key, got {status!r} ({detail})"
            print(f"  no-key   -> {status}: {detail}")
        finally:
            sign._mesh_keys_dir = orig_resolver

    print("PASS: sign/verify floor — ok, bad, unsigned, no-key all asserted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
