#!/usr/bin/env python3
"""test_keydist_slice1.py — gate for v0.3.0 key-distribution floor hardening.

Covers Slice 1 (A–H): fingerprint format/determinism, the --expect-fingerprint
gate, refuse-on-conflict --add + --rotate, doctor output, --export/--import
round-trip, crypto-absent keygen failing LOUD, and the Windows key-perms warning.

Most checks are stdlib-only (keyring/fingerprint/doctor need no `cryptography`),
so they run everywhere using a fixed valid public key; the one that mints a REAL
key is skipped when crypto is absent.

Run: python tests/test_keydist_slice1.py   (exit 0 = pass, 1 = fail)
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COMMS = REPO / "comms"
sys.path.insert(0, str(COMMS))
import sign    # noqa: E402  — fingerprint helper + CRYPTO_AVAILABLE
import keygen  # noqa: E402  — in-process checks for crypto-absent + perms-warn

KEYGEN_PY = COMMS / "keygen.py"
KEYRING_PY = COMMS / "keyring.py"
FINGERPRINT_PY = COMMS / "fingerprint.py"
DOCTOR_PY = COMMS / "doctor.py"

# Fixed, valid 32-byte "public keys" — let the keyring/fingerprint checks run
# WITHOUT cryptography (fingerprint is pure hashlib; keyring is pure JSON).
PUB = base64.b64encode(bytes(range(32))).decode("ascii")
PUB2 = base64.b64encode(bytes(range(1, 33))).decode("ascii")
EXPECT_FP = "synnoesis-fp:" + hashlib.sha256(bytes(range(32))).hexdigest()

_failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"PASS  {label}")
    else:
        print(f"FAIL  {label}" + (f"  ({detail})" if detail else ""))
        _failures.append(label)


def _run(args, env):
    return subprocess.run([sys.executable, *args], env=env,
                          capture_output=True, text=True)


def _env(home, agent=None):
    e = dict(os.environ)
    e["PA_HOME"] = str(home)
    if agent:
        e["PA_AGENT_ID"] = agent
    else:
        e.pop("PA_AGENT_ID", None)
    return e


def _ring(home) -> dict:
    """Read the keyring file directly (independent of any in-process state)."""
    try:
        return json.loads((home / "mesh-keyring.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"agents": {}}


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:  # noqa: BLE001
        return True


def _parse_pub(stdout: str):
    for line in stdout.splitlines():
        if "public key" in line and ":" in line:
            cand = line.split(":", 1)[1].strip()
            if cand:
                return cand
    return None


def _keygen_rc_crypto_absent() -> int:
    """keygen.cmd_keygen with CRYPTO_AVAILABLE forced False — must fail loud (2)."""
    import argparse
    saved = sign.CRYPTO_AVAILABLE
    sign.CRYPTO_AVAILABLE = False
    try:
        ns = argparse.Namespace(agent_id="zz", force=False)
        with contextlib.redirect_stderr(io.StringIO()):
            return keygen.cmd_keygen(ns)
    finally:
        sign.CRYPTO_AVAILABLE = saved


def _perms_warns(osname: str) -> bool:
    """Drive keygen._restrict_perms with os.name forced to `osname`; True iff it
    emitted the WARNING (i.e. detected chmod can't restrict on this OS)."""
    buf = io.StringIO()
    fd, name = tempfile.mkstemp(prefix="syn-perm-")
    os.close(fd)  # close the descriptor or Windows won't let us unlink it
    tf = Path(name)
    saved = keygen.os.name
    keygen.os.name = osname
    try:
        with contextlib.redirect_stderr(buf):
            keygen._restrict_perms(tf)
        return "WARNING" in buf.getvalue()
    finally:
        keygen.os.name = saved
        tf.unlink(missing_ok=True)


def _rm(path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


def main() -> int:
    # 1. fingerprint format + determinism (the documented bytes)
    fp = sign.pubkey_fingerprint(PUB)
    check("fingerprint matches documented format (synnoesis-fp: + sha256 hex)",
          fp == EXPECT_FP, f"{fp!r} != {EXPECT_FP!r}")
    check("fingerprint is deterministic", sign.pubkey_fingerprint(PUB) == fp)
    check("fingerprint rejects non-base64 input",
          _raises(lambda: sign.pubkey_fingerprint("@@@ not base64 @@@")))

    home = Path(tempfile.mkdtemp(prefix="syn-kd-"))
    try:
        # 2. fingerprint.py --pubkey
        r = _run([str(FINGERPRINT_PY), "--pubkey", PUB], _env(home))
        check("fingerprint.py --pubkey prints the fingerprint",
              r.returncode == 0 and EXPECT_FP in r.stdout,
              f"rc={r.returncode} out={r.stdout.strip()!r}")

        # 3. --expect-fingerprint match -> add ok
        r = _run([str(KEYRING_PY), "--add", "b", "--pubkey", PUB,
                  "--expect-fingerprint", EXPECT_FP], _env(home))
        check("keyring --add with MATCHING --expect-fingerprint exits 0",
              r.returncode == 0, f"rc={r.returncode} err={r.stderr.strip()!r}")

        # 4. --expect-fingerprint mismatch -> REFUSE (the gate)
        r = _run([str(KEYRING_PY), "--add", "cc", "--pubkey", PUB,
                  "--expect-fingerprint", "synnoesis-fp:deadbeef"], _env(home))
        check("keyring --add REFUSES on fingerprint MISMATCH (nonzero)",
              r.returncode != 0, f"rc={r.returncode}")
        check("mismatched key is NOT written",
              "cc" not in (_ring(home).get("agents") or {}))

        # 5. refuse-on-conflict + --rotate
        h2 = Path(tempfile.mkdtemp(prefix="syn-kd2-"))
        try:
            check("add a new key exits 0",
                  _run([str(KEYRING_PY), "--add", "x", "--pubkey", PUB],
                       _env(h2)).returncode == 0)
            r = _run([str(KEYRING_PY), "--add", "x", "--pubkey", PUB2], _env(h2))
            check("re-add a DIFFERENT key WITHOUT --rotate REFUSES",
                  r.returncode != 0, f"rc={r.returncode}")
            check("the conflicting key is not written",
                  (_ring(h2).get("agents") or {}).get("x", {}).get("pubkey") == PUB)
            r = _run([str(KEYRING_PY), "--add", "x", "--pubkey", PUB2, "--rotate"],
                     _env(h2))
            check("re-add a DIFFERENT key WITH --rotate exits 0",
                  r.returncode == 0, f"rc={r.returncode} err={r.stderr.strip()!r}")
            check("rotate wrote the new key",
                  (_ring(h2).get("agents") or {}).get("x", {}).get("pubkey") == PUB2)
            r = _run([str(KEYRING_PY), "--add", "x", "--pubkey", PUB2], _env(h2))
            check("re-add the SAME key is idempotent (exit 0, no --rotate)",
                  r.returncode == 0, f"rc={r.returncode}")
        finally:
            _rm(h2)

        # 6. doctor
        (home / "mesh-keys").mkdir(parents=True, exist_ok=True)
        (home / "mesh-keys" / "alice.key").write_text("x\n", encoding="utf-8")
        r = _run([str(DOCTOR_PY)], _env(home, agent="alice"))
        out = r.stdout
        check("doctor exits 0", r.returncode == 0, f"rc={r.returncode}")
        check("doctor reports the resolved PA_HOME", str(home) in out, out)
        check("doctor reports the agent id", "alice" in out, out)
        check("doctor reports the private key as present", "present" in out, out)
        check("doctor reports cryptography state",
              ("available" in out or "MISSING" in out), out)

        # 7. export/import round-trip
        exp = home / "ring.txt"
        _run([str(KEYRING_PY), "--add", "p", "--pubkey", PUB], _env(home))
        _run([str(KEYRING_PY), "--add", "q", "--pubkey", PUB2], _env(home))
        r = _run([str(KEYRING_PY), "--export", str(exp)], _env(home))
        check("export exits 0 and writes a file",
              r.returncode == 0 and exp.is_file(), f"rc={r.returncode}")
        h3 = Path(tempfile.mkdtemp(prefix="syn-kd3-"))
        try:
            r = _run([str(KEYRING_PY), "--import", str(exp)], _env(h3))
            ring = _ring(h3).get("agents") or {}
            check("import exits 0", r.returncode == 0, f"rc={r.returncode}")
            check("import round-trips key p", ring.get("p", {}).get("pubkey") == PUB)
            check("import round-trips key q", ring.get("q", {}).get("pubkey") == PUB2)
        finally:
            _rm(h3)

        # 8. crypto-absent -> keygen fails LOUD (in-process, deterministic)
        check("keygen fails loud (rc=2) when cryptography is absent",
              _keygen_rc_crypto_absent() == 2)

        # 9. Windows key-perms WARN fires when chmod can't restrict
        check("perms-warn fires on nt (chmod is a confidentiality no-op)",
              _perms_warns("nt"))
        check("perms-warn stays silent on posix (chmod honored)",
              not _perms_warns("posix"))

        # 9b. SF-C: on REAL posix, verify the EFFECT (perms actually restricted),
        # not just that the warn branch fires — verify-effect-not-proxy (matters on
        # the MacBook / posix CI). The nt WARN above is a HEURISTIC, not a
        # guarantee: Windows perms rely on the user-dir ACL, which this can't check.
        if os.name == "posix":
            _fd, _nm = tempfile.mkstemp(prefix="syn-perm-real-")
            os.close(_fd)
            _tf = Path(_nm)
            try:
                keygen._restrict_perms(_tf)  # real posix -> chmod honored
                _mode = os.stat(_tf).st_mode
                check("posix: _restrict_perms actually restricts (mode & 0o077 == 0)",
                      (_mode & 0o077) == 0, f"mode={oct(_mode & 0o777)}")
            finally:
                _tf.unlink(missing_ok=True)
        else:
            print("SKIP  posix perms-effect check (nt: chmod can't restrict; the "
                  "warn is the honest heuristic)")

        # 10. keygen prints the fingerprint (needs crypto to mint a real key)
        if sign.CRYPTO_AVAILABLE:
            r = _run([str(KEYGEN_PY), "--agent-id", "kg"], _env(home, agent="kg"))
            check("keygen prints a synnoesis-fp fingerprint",
                  "synnoesis-fp:" in r.stdout, r.stdout)
            pub = _parse_pub(r.stdout)
            if pub:
                check("keygen's printed fingerprint matches the minted key",
                      sign.pubkey_fingerprint(pub) in r.stdout)
        else:
            print("SKIP  keygen-prints-fingerprint (cryptography absent)")

        # 11. write-path validation (M1): malformed keys must never be pinned
        h4 = Path(tempfile.mkdtemp(prefix="syn-kd4-"))
        try:
            r = _run([str(KEYRING_PY), "--add", "g", "--pubkey", "@@@bad@@@"],
                     _env(h4))
            check("--add non-base64 pubkey REFUSES (rc!=0)", r.returncode != 0)
            check("--add non-base64 pubkey is NOT stored",
                  "g" not in (_ring(h4).get("agents") or {}))
            short = base64.b64encode(bytes(16)).decode("ascii")
            r = _run([str(KEYRING_PY), "--add", "g", "--pubkey", short], _env(h4))
            check("--add wrong-length (16B) key REFUSES (rc!=0)",
                  r.returncode != 0, f"rc={r.returncode}")
            r = _run([str(KEYRING_PY), "--add", "weird id", "--pubkey", PUB],
                     _env(h4))
            check("--add whitespace agent-id REFUSES (rc!=0)", r.returncode != 0)
        finally:
            _rm(h4)

        # 12. corrupt keyring (M2): a write must NOT silently clobber it
        h5 = Path(tempfile.mkdtemp(prefix="syn-kd5-"))
        try:
            corrupt = h5 / "mesh-keyring.json"
            corrupt.write_text("{ this is not valid json", encoding="utf-8")
            before = corrupt.read_text(encoding="utf-8")
            r = _run([str(KEYRING_PY), "--add", "g", "--pubkey", PUB], _env(h5))
            check("--add onto a CORRUPT keyring REFUSES (rc!=0)",
                  r.returncode != 0, f"rc={r.returncode}")
            check("corrupt keyring left UNCHANGED (no silent clobber)",
                  corrupt.read_text(encoding="utf-8") == before)
        finally:
            _rm(h5)

        # 13. import skips malformed (4-token) + invalid-key lines, keeps valid
        h6 = Path(tempfile.mkdtemp(prefix="syn-kd6-"))
        try:
            imp = h6 / "in.txt"
            imp.write_text(
                f"good ed25519 {PUB}\n"
                f"weird id ed25519 {PUB2}\n"        # 4 tokens -> skip
                "bad ed25519 @@@notbase64@@@\n",    # invalid key -> skip
                encoding="utf-8")
            _run([str(KEYRING_PY), "--import", str(imp)], _env(h6))
            ring = _ring(h6).get("agents") or {}
            check("import keeps the valid line", ring.get("good", {}).get("pubkey") == PUB)
            check("import skips the 4-token line",
                  "weird" not in ring and "id" not in ring)
            check("import skips the invalid-key line", "bad" not in ring)
        finally:
            _rm(h6)

        # 14. doctor ABSENT-key branch + --export to stdout
        h7 = Path(tempfile.mkdtemp(prefix="syn-kd7-"))
        try:
            r = _run([str(DOCTOR_PY)], _env(h7, agent="nope"))
            check("doctor reports an ABSENT private key", "ABSENT" in r.stdout, r.stdout)
        finally:
            _rm(h7)
        _run([str(KEYRING_PY), "--add", "s1", "--pubkey", PUB], _env(home))
        r = _run([str(KEYRING_PY), "--export"], _env(home))
        check("--export to stdout prints a known_hosts-shape line",
              r.returncode == 0 and f"s1 ed25519 {PUB}" in r.stdout, r.stdout)

        # 15. SF-A: atomic _write leaves no .tmp sibling behind (os.replace ran)
        check("atomic _write leaves no leftover .tmp",
              not (home / "mesh-keyring.json.tmp").exists())
    finally:
        _rm(home)

    print()
    if _failures:
        print(f"RESULT  FAIL  ({len(_failures)} failed: {', '.join(_failures)})")
        return 1
    print("RESULT  PASS  (key-distribution Slice 1 floor hardening)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
