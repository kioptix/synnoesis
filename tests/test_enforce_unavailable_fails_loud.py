#!/usr/bin/env python3
"""test_enforce_unavailable_fails_loud.py -- gate #3: enforce + no-crypto fails LOUD.

If ``SYN_ENFORCE_SIGNING`` is set but the ``cryptography`` library is absent, the
verifier reports every record as ``unavailable``. Silently suppressing 100% of
traffic in that state is the "absence masquerading as success" failure mode -- a
user who asked to enforce would get a SILENT EMPTY inbox and believe there were no
messages. The contract instead requires a LOUD one-line stderr config-error and a
NON-ZERO exit at startup.

Simulating absent-crypto honestly (not by uninstalling the real lib): we run
``inbox.py`` in a subprocess whose ``sys.path`` is fronted by a shim directory
holding a ``cryptography.py`` that raises ImportError on import. ``sign.py``'s
``try: from cryptography...`` then fails exactly as on a host without the package,
so ``sign.CRYPTO_AVAILABLE`` is False -- the real code path under test, reached via
the real import machinery, not a monkeypatch of the value.

We also assert the NEGATIVE: with crypto "absent" we seed a record in the inbox and
prove enforce does NOT silently print "(no messages)" + exit 0 -- it must refuse.

Run: python tests/test_enforce_unavailable_fails_loud.py   (exit 0 = pass, 1 = fail)
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
INBOX_PY = COMMS / "inbox.py"

_failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"PASS  {label}")
    else:
        suffix = f"  ({detail})" if detail else ""
        print(f"FAIL  {label}{suffix}")
        _failures.append(label)


# A stand-in 'cryptography' module that raises on import, so the real
# `try: from cryptography... import ...` in sign.py fails -> CRYPTO_AVAILABLE=False.
_SHIM = (
    "raise ImportError("
    "\"simulated-absent cryptography (test_enforce_unavailable_fails_loud)\")\n"
)


def main() -> int:
    home = Path(tempfile.mkdtemp(prefix="syn-unavail-"))
    shim = Path(tempfile.mkdtemp(prefix="syn-cryptoshim-"))
    comms_dir = home / "comms"
    comms_dir.mkdir(parents=True, exist_ok=True)
    # A fake cryptography package the shim dir shadows the real one with. Use a
    # package dir so `from cryptography.hazmat...` also fails at the top import.
    pkg = shim / "cryptography"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(_SHIM, encoding="utf-8")

    # Seed a record so a "silent empty read" would otherwise look like success.
    inbox_path = comms_dir / "bob-inbox.jsonl"
    seeded = {
        "topic": "agent/bob/inbox",
        "received_at": "2026-06-28T12:00:00+00:00",
        "payload": json.dumps({
            "_urgency": "normal", "_from": "alice", "_to": "bob",
            "_at": "2026-06-28T12:00:00+00:00", "_nonce": "feedfacefeedface",
            "body": "SHOULD-NOT-SILENTLY-VANISH",
        }, ensure_ascii=False),
        "_verify": {"status": "unsigned", "detail": "seeded"},
    }
    with inbox_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(seeded, ensure_ascii=False) + "\n")

    try:
        env = dict(os.environ)
        env["PA_HOME"] = str(home)
        env["SYN_ENFORCE_SIGNING"] = "1"
        # Front sys.path with the shim so import cryptography hits our raiser.
        env["PYTHONPATH"] = str(shim) + os.pathsep + env.get("PYTHONPATH", "")

        proc = subprocess.run(
            [sys.executable, str(INBOX_PY), "--agent-id", "bob",
             "--since", "1d", "--not-from", "none"],
            env=env, capture_output=True, text=True)

        # Sanity: the shim actually took effect (sign degraded). If crypto still
        # loaded, this whole test is meaningless -- assert the precondition.
        probe = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, r'%s'); sys.path.insert(0, r'%s'); "
             "import sign; print('CRYPTO_AVAILABLE', sign.CRYPTO_AVAILABLE)"
             % (str(shim), str(COMMS))],
            env=env, capture_output=True, text=True)
        check("precondition: shim makes sign.CRYPTO_AVAILABLE False",
              "CRYPTO_AVAILABLE False" in probe.stdout,
              f"probe stdout={probe.stdout.strip()!r} stderr={probe.stderr.strip()!r}")

        check("enforce+no-crypto exits NON-ZERO", proc.returncode != 0,
              f"rc={proc.returncode}")
        check("stderr carries a config-error mentioning cryptography",
              "cryptography" in proc.stderr.lower(),
              f"stderr={proc.stderr.strip()!r}")
        check("stderr config-error mentions SYN_ENFORCE_SIGNING",
              "SYN_ENFORCE_SIGNING" in proc.stderr,
              f"stderr={proc.stderr.strip()!r}")
        # The NEGATIVE: it must NOT silently print the seeded message nor a clean
        # "no messages" line and exit 0 (that's the black-hole failure mode).
        check("did NOT silently deliver the seeded record",
              "SHOULD-NOT-SILENTLY-VANISH" not in proc.stdout,
              f"stdout={proc.stdout.strip()!r}")
        check("did NOT silently report an empty/clean inbox on stdout",
              "no inbound mesh messages" not in proc.stdout,
              f"stdout={proc.stdout.strip()!r}")
    finally:
        import shutil
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(shim, ignore_errors=True)

    print()
    if _failures:
        print(f"RESULT  FAIL  ({len(_failures)} failed: {', '.join(_failures)})")
        return 1
    print("RESULT  PASS  (enforce + absent crypto -> loud config-error, non-zero, no silent black-hole)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
