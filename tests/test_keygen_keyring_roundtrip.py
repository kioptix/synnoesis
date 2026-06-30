#!/usr/bin/env python3
"""test_keygen_keyring_roundtrip.py -- gate #4: keygen -> keyring --add -> ok verify.

Drives the two new thin CLIs end-to-end exactly as quickstart Section 5 tells a
user to, through subprocess (as a consumer invokes them), then proves the result:
a message signed with the freshly-minted key verifies ``ok`` through the real floor.

  1. ``python comms/keygen.py --agent-id alice``  -> writes alice's private key
     born-local under PA_HOME, prints alice's PUBLIC key.
  2. ``python comms/keyring.py --add alice --pubkey <that pubkey>``  -> records the
     trust decision in the local keyring (the deliberate "I believe alice" step).
  3. ``python comms/send.py --to bob "..."`` as alice -> send.py loads alice's
     private key, signs, verifies against the keyring it just populated, and stamps
     ``verify=ok`` on the record.
  4. ``python comms/inbox.py --agent-id bob`` -> the delivered line has NO
     ``[!...]`` trust marker (markers fire only for non-ok), i.e. the message reads
     as verified. We ALSO assert the stamped status directly from the JSONL to be
     unambiguous (no reliance on the absence of a marker alone).

All four steps share ONE throwaway ``PA_HOME`` so keygen, keyring, send, and read
resolve the same mesh-keys dir + keyring file -- the same chaining a real user does.

Run: python tests/test_keygen_keyring_roundtrip.py   (exit 0 = pass, 1 = fail; SKIP no-crypto)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COMMS = REPO / "comms"
sys.path.insert(0, str(COMMS))
import sign  # noqa: E402  -- only for the CRYPTO_AVAILABLE skip gate

KEYGEN_PY = COMMS / "keygen.py"
KEYRING_PY = COMMS / "keyring.py"
SEND_PY = COMMS / "send.py"
INBOX_PY = COMMS / "inbox.py"

SENDER = "alice"
RECIPIENT = "bob"
BODY = "signed-and-verified-please"

_failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"PASS  {label}")
    else:
        suffix = f"  ({detail})" if detail else ""
        print(f"FAIL  {label}{suffix}")
        _failures.append(label)


def _run(args: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], env=env,
                          capture_output=True, text=True)


def _parse_pubkey(keygen_stdout: str) -> str | None:
    """keygen prints a line: '  public key : <base64>'. Pull the base64 out."""
    for line in keygen_stdout.splitlines():
        if "public key" in line and ":" in line:
            cand = line.split(":", 1)[1].strip()
            if cand:
                return cand
    return None


def main() -> int:
    if not sign.CRYPTO_AVAILABLE:
        print("SKIP: cryptography unavailable -- keygen cannot mint a key.")
        return 0
    for f in (KEYGEN_PY, KEYRING_PY):
        if not f.is_file():
            check(f"{f.name} present", False, f"missing {f}")
            return 1

    home = Path(tempfile.mkdtemp(prefix="syn-keygen-"))
    env = dict(os.environ)
    env["PA_HOME"] = str(home)
    env["PA_AGENT_ID"] = SENDER
    try:
        # 1. keygen
        kg = _run([str(KEYGEN_PY), "--agent-id", SENDER], env)
        check("keygen exits 0", kg.returncode == 0,
              f"rc={kg.returncode} stderr={kg.stderr.strip()!r}")
        priv_path = home / "mesh-keys" / f"{SENDER}.key"
        check("keygen wrote the born-local private key", priv_path.is_file(),
              f"expected {priv_path}")
        pub = _parse_pubkey(kg.stdout)
        check("keygen printed a public key", bool(pub),
              f"stdout={kg.stdout.strip()!r}")
        if not pub:
            return 1

        # 2. keyring --add (the trust decision)
        kr = _run([str(KEYRING_PY), "--add", SENDER, "--pubkey", pub], env)
        check("keyring --add exits 0", kr.returncode == 0,
              f"rc={kr.returncode} stderr={kr.stderr.strip()!r}")
        keyring_file = home / "mesh-keyring.json"
        check("keyring file written", keyring_file.is_file(),
              f"expected {keyring_file}")
        if keyring_file.is_file():
            ring = json.loads(keyring_file.read_text(encoding="utf-8"))
            check("registered pubkey matches keygen's",
                  ring.get("agents", {}).get(SENDER, {}).get("pubkey") == pub,
                  f"ring={ring}")

        # 3. send (signs with alice's key, verifies against the keyring)
        sd = _run([str(SEND_PY), "--to", RECIPIENT, BODY], env)
        check("send exits 0", sd.returncode == 0,
              f"rc={sd.returncode} stderr={sd.stderr.strip()!r}")
        check("send reports verify=ok", "verify=ok" in sd.stdout,
              f"stdout={sd.stdout.strip()!r}")

        # 4. read -- assert the stamped status on disk is ok, and the printed
        #    line carries NO non-ok trust marker.
        inbox_path = home / "comms" / f"{RECIPIENT}-inbox.jsonl"
        check("recipient inbox written", inbox_path.is_file(),
              f"expected {inbox_path}")
        if inbox_path.is_file():
            lines = [ln for ln in inbox_path.read_text(encoding="utf-8").splitlines()
                     if ln.strip()]
            rec = json.loads(lines[-1]) if lines else {}
            check("on-disk verify status is ok",
                  rec.get("_verify", {}).get("status") == "ok",
                  f"status={rec.get('_verify', {}).get('status')!r}")

        rd = _run([str(INBOX_PY), "--agent-id", RECIPIENT,
                   "--since", "1d", "--not-from", "none"], env)
        check("read exits 0", rd.returncode == 0, f"rc={rd.returncode}")
        check("read delivers the body", BODY in rd.stdout,
              f"stdout={rd.stdout.strip()!r}")
        # No non-ok markers on the verified line.
        for mark in ("[!UNSIGNED]", "[!NO-KEY]", "[!BAD]", "[!UNVERIFIED]",
                     "[!UNAVAILABLE]"):
            check(f"read shows NO {mark} marker (message verified)",
                  mark not in rd.stdout,
                  f"stdout={rd.stdout.strip()!r}")
    finally:
        import shutil
        shutil.rmtree(home, ignore_errors=True)

    print()
    if _failures:
        print(f"RESULT  FAIL  ({len(_failures)} failed: {', '.join(_failures)})")
        return 1
    print("RESULT  PASS  (keygen -> keyring --add -> signed message verifies ok through the floor)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
