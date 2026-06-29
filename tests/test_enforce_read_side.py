#!/usr/bin/env python3
"""test_enforce_read_side.py -- gate #2: SYN_ENFORCE_SIGNING read-side suppression.

With ``SYN_ENFORCE_SIGNING=1`` the inbox reader must:
  * DELIVER records whose stamped ``_verify.status == "ok"``;
  * SUPPRESS (not print) ``bad`` / ``unsigned`` / ``no-key`` records;
  * ALWAYS emit ``# enforce: suppressed N unverified record(s)`` on stderr when
    >= 1 record was suppressed (never silently drop traffic);
  * keep exit code 0 (the inbox read-only diagnostic invariant).

The records are produced by the REAL chain, not hand-stamped: we keygen a real
keypair, register it, and ``send.py`` then stamps an honest ``ok`` verdict (it
verifies the locally-signed message against the keyring). The three failing
records are produced by sending UNDER conditions that yield each non-ok verdict:
  * ``unsigned`` -- send with no private key on disk (warn-phase unsigned send);
  * ``no-key``   -- send signed, but with an EMPTY keyring (sender unknown);
  * ``bad``      -- take a real signed record and tamper the body so the stored
                    signature no longer matches, then re-stamp the verdict via the
                    real ``sign.verify_against_keyring``.
All four land in one recipient inbox; then ``inbox.py`` is run as a subprocess
(as a consumer invokes it) once OFF and once ON, and we assert the matrix.

Run: python tests/test_enforce_read_side.py   (exit 0 = pass, 1 = fail; SKIP if no crypto)
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
import sign   # noqa: E402
import wire   # noqa: E402

INBOX_PY = COMMS / "inbox.py"

_failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"PASS  {label}")
    else:
        suffix = f"  ({detail})" if detail else ""
        print(f"FAIL  {label}{suffix}")
        _failures.append(label)


def _append(inbox_path: Path, outer: dict) -> None:
    with inbox_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(outer, ensure_ascii=False) + "\n")


def _run_inbox(home: Path, agent: str, enforce: bool):
    env = dict(os.environ)
    env["PA_HOME"] = str(home)
    # Read EVERYTHING: include fyi/state off by default is fine (we use normal
    # urgency). --not-from empty so our synthetic senders aren't excluded.
    if enforce:
        env["SYN_ENFORCE_SIGNING"] = "1"
    else:
        env.pop("SYN_ENFORCE_SIGNING", None)
    return subprocess.run(
        [sys.executable, str(INBOX_PY), "--agent-id", agent,
         "--since", "1d", "--not-from", "none"],
        env=env, capture_output=True, text=True)


def main() -> int:
    if not sign.CRYPTO_AVAILABLE:
        print("SKIP: cryptography unavailable -- cannot produce an 'ok' record.")
        return 0

    sender = "alice"
    recipient = "bob"
    home = Path(tempfile.mkdtemp(prefix="syn-enforce-"))
    comms_dir = home / "comms"
    comms_dir.mkdir(parents=True, exist_ok=True)
    mesh_keys = home / "mesh-keys"
    mesh_keys.mkdir(parents=True, exist_ok=True)
    keyring_path = home / "mesh-keyring.json"
    inbox_path = comms_dir / f"{recipient}-inbox.jsonl"

    # Redirect sign's key/keyring resolver at our temp tree (no env hook exists).
    orig_resolver = sign._mesh_keys_dir
    sign._mesh_keys_dir = lambda: mesh_keys
    try:
        priv_b64, pub_b64 = sign.generate_keypair()
        (mesh_keys / f"{sender}.key").write_text(priv_b64, encoding="utf-8")
        full_ring = {"agents": {sender: {"pubkey": pub_b64}}}
        empty_ring = {"agents": {}}
        keyring_path.write_text(json.dumps(full_ring, indent=2), encoding="utf-8")

        # --- ok: real signed message, verified against the full keyring -------
        ok_inner = sign.new_message(sender, recipient, "OK-DELIVER-ME", "normal")
        ok_status, ok_detail = sign.verify_against_keyring(
            ok_inner, full_ring, expected_tag=sign.DS_TAG_ENVELOPE)
        _append(inbox_path, wire.wrap_outer(ok_inner, ok_status, ok_detail))
        check("setup: ok record stamped ok", ok_status == "ok",
              f"got {ok_status!r}")

        # --- unsigned: assemble an envelope WITHOUT a signature ---------------
        uns_inner = {
            "_urgency": "normal", "_from": sender, "_to": recipient,
            "_at": ok_inner["_at"], "_nonce": "deadbeefdeadbeef",
            "body": "UNSIGNED-SUPPRESS-ME",
        }
        uns_status, uns_detail = sign.verify_against_keyring(
            uns_inner, full_ring, expected_tag=sign.DS_TAG_ENVELOPE)
        _append(inbox_path, wire.wrap_outer(uns_inner, uns_status, uns_detail))
        check("setup: unsigned record stamped unsigned", uns_status == "unsigned",
              f"got {uns_status!r}")

        # --- no-key: real signed message, but verified against EMPTY ring -----
        nk_inner = sign.new_message(sender, recipient, "NOKEY-SUPPRESS-ME", "normal")
        nk_status, nk_detail = sign.verify_against_keyring(
            nk_inner, empty_ring, expected_tag=sign.DS_TAG_ENVELOPE)
        _append(inbox_path, wire.wrap_outer(nk_inner, nk_status, nk_detail))
        check("setup: no-key record stamped no-key", nk_status == "no-key",
              f"got {nk_status!r}")

        # --- bad: real signed message, body tampered after signing ------------
        bad_inner = sign.new_message(sender, recipient, "ORIGINAL-BODY", "normal")
        bad_inner["body"] = "BAD-TAMPERED-SUPPRESS-ME"   # sig no longer matches
        bad_status, bad_detail = sign.verify_against_keyring(
            bad_inner, full_ring, expected_tag=sign.DS_TAG_ENVELOPE)
        _append(inbox_path, wire.wrap_outer(bad_inner, bad_status, bad_detail))
        check("setup: bad record stamped bad", bad_status == "bad",
              f"got {bad_status!r}")
    finally:
        sign._mesh_keys_dir = orig_resolver

    try:
        # --- OFF: warn-only, ALL four delivered, markers present --------------
        off = _run_inbox(home, recipient, enforce=False)
        check("OFF exits 0", off.returncode == 0,
              f"rc={off.returncode} stderr={off.stderr.strip()!r}")
        for body in ("OK-DELIVER-ME", "UNSIGNED-SUPPRESS-ME",
                     "NOKEY-SUPPRESS-ME", "BAD-TAMPERED-SUPPRESS-ME"):
            check(f"OFF delivers {body!r}", body in off.stdout)
        check("OFF emits NO enforce-summary line",
              "# enforce: suppressed" not in off.stderr,
              f"stderr={off.stderr.strip()!r}")

        # --- ON: only ok delivered; the other three suppressed ----------------
        on = _run_inbox(home, recipient, enforce=True)
        check("ON exits 0 (read-only invariant)", on.returncode == 0,
              f"rc={on.returncode}")
        check("ON DELIVERS the ok record", "OK-DELIVER-ME" in on.stdout)
        check("ON SUPPRESSES the unsigned record",
              "UNSIGNED-SUPPRESS-ME" not in on.stdout)
        check("ON SUPPRESSES the no-key record",
              "NOKEY-SUPPRESS-ME" not in on.stdout)
        check("ON SUPPRESSES the bad record",
              "BAD-TAMPERED-SUPPRESS-ME" not in on.stdout)
        # The headline: the suppression is announced, never silent.
        check("ON emits '# enforce: suppressed 3 unverified record(s)' on stderr",
              "# enforce: suppressed 3 unverified record(s)" in on.stderr,
              f"stderr={on.stderr.strip()!r}")
    finally:
        import shutil
        shutil.rmtree(home, ignore_errors=True)

    print()
    if _failures:
        print(f"RESULT  FAIL  ({len(_failures)} failed: {', '.join(_failures)})")
        return 1
    print("RESULT  PASS  (enforce delivers ok, suppresses 3, announces on stderr, exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
